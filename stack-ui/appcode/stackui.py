"""AI Ecosystem — the stack's control dashboard. Runs in the browser (Pyodide + wapyt).

Shape: a header toolbar (the backend switch and the whole-stack actions), a sidebar of
screens, and the screen itself. The home screen is the dashboard — backends and what they
have loaded on top, frontends below. The rest are under the sidebar: language models
(installed GGUFs + HuggingFace search), image models (ComfyUI files + Civitai search), the
VRAM planner, logs, custom-node pins and the doctor.

Conventions (shared with the Pytinc apps):

* BFF calls are ``await X().method_async(...)``; every result is ``{"ok": ...}``.
* The UI is built in ``load_ui``, never ``__init__``.
* Import *names* from modules, never modules — the browser packager follows only that.
* DOM misses are ``JsNull``: falsy, but not ``None``.

Screens are routed by the URL hash (``#/llms``) so a reload keeps your place.

One app-wide rule carries over from the old page: **while an action runs, every control
that could start another is disabled** — the server holds a single action slot and refuses
a second one, and the UI should never let you trip over that by accident.
"""
from __future__ import annotations

import js

from wapyt import (
    CellConfig,
    LayoutConfig,
    MainWindow,
    ProgressBar,
    ProgressBarConfig,
    SidebarConfig,
    SidebarHeading,
    SidebarItem,
    ToolbarButton,
    ToolbarConfig,
    ToolbarSeparator,
    ToolbarSpacer,
    ToolbarText,
    message,
)

from services.models_api import ModelsApi
from services.stack_api import StackApi
from ui.common import (Listeners, Stream, by_id, container_of, failed, fmt_bytes, link,
                       spawn, toast)
from ui.dashboard import DashboardView
from ui.images import ImagesView
from ui.llms import LlmsView
from ui.system import DoctorView, LogsView, NodesView
from ui.vramview import VramView

APP_ENTRYPOINT = "StackUI"

POLL_MS = 10_000

SCREENS = {
    "dashboard": DashboardView,
    "llms": LlmsView,
    "images": ImagesView,
    "vram": VramView,
    "logs": LogsView,
    "nodes": NodesView,
    "doctor": DoctorView,
}

# `stack install` runs its prerequisite check first, and nvidia-smi + the CDI spec cannot
# pass from inside this container — so it exits 1 even when the node work succeeded.
_INSTALL_NOTE = (
    "note: the non-zero exit is the GPU prerequisite check, which cannot pass from inside "
    "this container. The node work above it did run — check the lines above, or run "
    "`stack doctor` in a terminal.")


class StackUI(MainWindow):
    layout_config = LayoutConfig(rows=[
        CellConfig(id="header", height="auto"),
        CellConfig(id="dlbar", height="auto", hidden=True),
        CellConfig(id="body", grow=1, cols=[
            CellConfig(id="nav", width="224px"),
            CellConfig(id="main", width="100%", css="su-main"),
        ]),
        CellConfig(id="console", height="240px", hidden=True, resizable=True,
                   min_size="120px"),
    ])

    # --- construction ----------------------------------------------------------
    def load_ui(self):
        self.busy = False
        self.overview: dict = {}
        self.screen = None
        self.screen_id = ""
        self._action_stream = None
        self._dl_stream = None
        self._dl_bar = None
        self._listeners = Listeners()
        self._poll_proxy = None

        self._load_css()
        dark = bool(js.window.matchMedia("(prefers-color-scheme: dark)").matches)
        self.set_theme("dark" if dark else "light")
        js.document.title = "AI Ecosystem"

        self.toolbar = self.add_toolbar("header", ToolbarConfig(label="Stack", items=[
            ToolbarText(id="brand", text="AI Ecosystem"),
            ToolbarSeparator(),
            ToolbarText(id="switch", text="Switch …"),
            ToolbarButton(id="on", label="Turn on", icon="mdi-power", variant="primary",
                          hidden=True,
                          tooltip="Start the backends now and at every boot (stack on)"),
            ToolbarButton(id="off", label="Turn off", icon="mdi-power-off", hidden=True,
                          tooltip="Stop the backends and keep them off at boot (stack off)"),
            ToolbarSeparator(),
            ToolbarButton(id="up", label="Start backends", icon="mdi-play",
                          tooltip="Start KoboldCPP and ComfyUI without changing the switch"),
            ToolbarButton(id="desktop", label="Desktop avatar", icon="mdi-account-box",
                          tooltip="Pantheon's avatar on your desktop"),
            ToolbarButton(id="downall", label="Stop all", icon="mdi-stop", variant="danger",
                          tooltip="Stop every backend and frontend (not this dashboard)"),
            ToolbarSpacer(),
            ToolbarText(id="updated", text=""),
            ToolbarButton(id="refresh", icon="mdi-refresh", tooltip="Refresh now",
                          show_label=False, label="Refresh"),
        ]))
        self.toolbar.on_click(self._on_toolbar)

        self.sidebar = self.add_sidebar("nav", SidebarConfig(
            collapse_button=True,
            active="dashboard",
            items=[
                SidebarItem(id="dashboard", label="Dashboard", icon="mdi-view-dashboard-outline"),
                SidebarHeading(label="Models"),
                SidebarItem(id="llms", label="Language models",
                            icon="mdi-text-box-multiple-outline",
                            tooltip="Installed GGUFs for KoboldCPP, and HuggingFace search"),
                SidebarItem(id="images", label="Image models", icon="mdi-image-multiple-outline",
                            tooltip="ComfyUI checkpoints, LoRAs and VAEs, and Civitai search"),
                SidebarItem(id="vram", label="VRAM planner", icon="mdi-memory"),
                SidebarHeading(label="System"),
                SidebarItem(id="logs", label="Logs", icon="mdi-text-long"),
                SidebarItem(id="nodes", label="Custom nodes", icon="mdi-puzzle-outline"),
                SidebarItem(id="doctor", label="Doctor", icon="mdi-stethoscope"),
            ],
        ))
        self.sidebar.on_select(lambda p: self.go((p or {}).get("id") or "dashboard"))

        self.attach_html("dlbar", (
            '<div class="su-dl" id="su-dl">'
            '<i class="mdi mdi-download"></i>'
            '<span class="su-dl-label" id="su-dl-label"></span>'
            '<span id="su-dl-page"></span>'
            '<div class="su-dl-bar" id="su-dl-bar"></div>'
            '<span class="su-dim" id="su-dl-text"></span>'
            '<button class="su-btn" id="su-dl-cancel">Cancel</button>'
            '<button class="su-btn su-ghost" id="su-dl-close" hidden>Dismiss</button>'
            '</div>'))
        self.attach_html("console", (
            '<div class="su-console">'
            '<div class="su-console-head">'
            '<i class="mdi mdi-console"></i>'
            '<span id="su-console-title">Output</span>'
            '<span class="su-spin" id="su-console-spin" hidden></span>'
            '<span class="su-flex"></span>'
            '<button class="su-btn su-ghost" id="su-console-hide">Hide</button>'
            '</div>'
            '<pre id="su-console-out"></pre>'
            '</div>'))
        self._listeners.on(by_id("su-console-hide"), "click", lambda _e: self.hide("console"))
        self._listeners.on(by_id("su-dl-cancel"), "click",
                           lambda _e: spawn(self._cancel_download(), "cancel download"))
        self._listeners.on(by_id("su-dl-close"), "click", lambda _e: self.hide("dlbar"))
        self._listeners.on(js.window, "hashchange", lambda _e: self._route())

        self._route()
        spawn(self._resume(), "resume")
        self._poll_proxy = js.setInterval(_proxy(self._tick), POLL_MS)

    def _load_css(self) -> None:
        if by_id("su-css"):
            return
        el = js.document.createElement("link")
        el.id = "su-css"
        el.rel = "stylesheet"
        el.href = "/static/stackui.css?v=1"
        js.document.head.appendChild(el)

    # --- routing ---------------------------------------------------------------
    def go(self, screen_id: str, **kwargs) -> None:
        """Switch screens. kwargs reach the screen (e.g. ``go("logs", app="comfyui")``)."""
        self._pending_args = kwargs
        target = f"#/{screen_id}"
        if js.window.location.hash != target:
            js.window.location.hash = target          # -> hashchange -> _route
        else:
            self._route()

    def _route(self) -> None:
        raw = str(js.window.location.hash or "").lstrip("#/")
        screen_id = raw if raw in SCREENS else "dashboard"
        args = getattr(self, "_pending_args", None) or {}
        self._pending_args = {}
        if self.screen and self.screen_id == screen_id:
            if args and hasattr(self.screen, "apply_args"):
                self.screen.apply_args(**args)
            return
        if self.screen:
            try:
                self.screen.unmount()
            except Exception:  # noqa: BLE001
                js.console.error(f"[stackui] unmount {self.screen_id} failed")
        main = container_of(self.get_cell("main"))
        main.innerHTML = ""
        host = js.document.createElement("div")
        host.className = "su-screen"
        main.appendChild(host)
        self.screen_id = screen_id
        self.screen = SCREENS[screen_id](self, **args)
        self.screen.mount(host)
        if self.overview and hasattr(self.screen, "on_overview"):
            self.screen.on_overview(self.overview)
        if self.sidebar.get_active() != screen_id:
            self.sidebar.set_active(screen_id)

    # --- polling ---------------------------------------------------------------
    def _tick(self, *_):
        # Not over a running action: its output stream is the live view, and a refresh
        # mid-action only fights the disabled-button state.
        if not self.busy and not js.document.hidden:
            spawn(self.refresh(), "refresh")

    async def refresh(self) -> None:
        ov = await StackApi().overview_async()
        if not isinstance(ov, dict) or not ov.get("ok"):
            self.toolbar.set_text("switch", "stack status failed")
            self.toolbar.set_text("updated", (ov or {}).get("error", "") if isinstance(ov, dict) else "")
            return
        self.overview = ov
        sw = ov.get("switch")
        self.toolbar.set_text("switch", f"Backends {sw}" if sw else "Switch unknown")
        self.toolbar.set_hidden("on", sw == "ON")
        self.toolbar.set_hidden("off", sw != "ON")
        self.toolbar.set_text("desktop", "Close avatar" if ov.get("desktop") else "Desktop avatar")
        self.toolbar.set_text("updated", f"updated {js.Date.new().toLocaleTimeString()} · "
                                         f"{ov.get('duration_ms')} ms")
        # A job started in another tab (or before a reload) still owns the slot: follow it
        # rather than letting this page think it is idle.
        if ov.get("busy") and ov.get("job") and not self._action_stream:
            self._follow(ov["job"])
        if self.screen and hasattr(self.screen, "on_overview"):
            self.screen.on_overview(ov)

    async def _resume(self) -> None:
        await self.refresh()
        state = await ModelsApi().download_state_async()
        d = (state or {}).get("download") if isinstance(state, dict) else None
        if d and d.get("state") == "running":
            self.watch_download(d)

    # --- actions ---------------------------------------------------------------
    def _on_toolbar(self, p) -> None:
        item = (p or {}).get("id")
        if item == "refresh":
            spawn(self.refresh(), "refresh")
        elif item == "on":
            self.act("on")
        elif item == "off":
            self.act("off", confirm="Stop both backends and disable boot auto-start?")
        elif item == "up":
            self.act("up")
        elif item == "desktop":
            # One toggle: `stack desktop` is idempotent, so a stray second press is harmless.
            if self.overview.get("desktop"):
                self.act("desktop_stop", confirm="Close the desktop avatar?")
            else:
                self.act("desktop")
        elif item == "downall":
            self.act("down", "all", confirm="Stop all backends and frontends?")

    def act(self, verb: str, target: str = "", confirm: str = "") -> None:
        if self.busy:
            toast("Another action is still running.", "warning")
            return
        spawn(self._act(verb, target, confirm), f"action {verb}")

    async def _act(self, verb: str, target: str, confirm: str) -> None:
        if confirm and not await message.confirm(confirm, title="Confirm",
                                                 ok_text="Go ahead"):
            return
        r = await StackApi().action_async(verb, target or "")
        if failed(r, f"run stack {verb}"):
            if isinstance(r, dict) and r.get("busy"):
                spawn(self.refresh(), "refresh")
            return
        self._follow(r["job"])

    def _follow(self, job: dict) -> None:
        self.set_busy(True)
        self.show("console")
        out = by_id("su-console-out")
        out.textContent = ""
        by_id("su-console-title").textContent = job.get("label") or "stack"
        by_id("su-console-spin").hidden = False
        if self._action_stream:
            self._action_stream.close()

        def on_event(ev: dict) -> None:
            if ev.get("type") == "line":
                out.textContent += (ev.get("text") or "") + "\n"
            elif ev.get("type") == "done":
                if ev.get("error"):
                    out.textContent += f"\n!! {ev['error']}\n"
                else:
                    out.textContent += f"\n[exit {ev.get('exit_code')}]\n"
                if job.get("verb") == "install_comfyui" and ev.get("exit_code") != 0:
                    out.textContent += _INSTALL_NOTE + "\n"
                self._action_stream.close()
                self._done()
            out.scrollTop = out.scrollHeight

        self._action_stream = Stream(f"/api/action/{js.encodeURIComponent(job['id'])}/events",
                                     on_event, on_end=self._done)

    def _done(self) -> None:
        self._action_stream = None
        by_id("su-console-spin").hidden = True
        self.set_busy(False)
        spawn(self.refresh(), "refresh")

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        for item in ("on", "off", "up", "desktop", "downall"):
            self.toolbar.set_disabled(item, busy)
        if self.screen and hasattr(self.screen, "on_busy"):
            self.screen.on_busy(busy)

    def open_logs(self, app: str) -> None:
        self.go("logs", app=app)

    # --- downloads -------------------------------------------------------------
    def watch_download(self, d: dict) -> None:
        self.show("dlbar")
        if self._dl_bar is None:
            self._dl_bar = ProgressBar(ProgressBarConfig(compact=True, show_value=True),
                                       container=by_id("su-dl-bar"))
        by_id("su-dl-cancel").hidden = False
        by_id("su-dl-close").hidden = True
        if self._dl_stream:
            self._dl_stream.close()
        self._dl_stream = Stream(f"/api/downloads/{js.encodeURIComponent(d['id'])}/events",
                                 self._on_download, on_end=self._dl_ended)
        self._dl_current = d
        self._on_download(d)

    def _on_download(self, p: dict) -> None:
        self._dl_current = p
        by_id("su-dl-label").textContent = p.get("name") or p.get("label") or ""
        # The one place that knows the origin for certain: it is the URL being fetched.
        # Shown on failure too — the page is where you find out a repo is gated.
        by_id("su-dl-page").innerHTML = link(p["page"], "page") if p.get("page") else ""
        state = p.get("state")
        bar = self._dl_bar
        if state == "running":
            if p.get("total"):
                bar.set_value(p.get("received") or 0, p["total"])
            else:
                bar.set_indeterminate(True)
            rate = f" · {fmt_bytes(p['rate'])}/s" if p.get("rate") else ""
            eta = f" · {_eta(p['eta'])} left" if p.get("eta") else ""
            total = f" / {fmt_bytes(p['total'])}" if p.get("total") else ""
            by_id("su-dl-text").textContent = f"{fmt_bytes(p.get('received'))}{total}{rate}{eta}"
            return
        by_id("su-dl-cancel").hidden = True
        by_id("su-dl-close").hidden = False
        if state == "done":
            bar.set_value(1, 1)
            bar.set_state("done")
            by_id("su-dl-text").textContent = f"done — {fmt_bytes(p.get('received'))}"
            toast(f"Downloaded {p.get('name')}", "success")
        else:
            bar.set_state("error" if state != "cancelled" else "paused")
            by_id("su-dl-text").textContent = (
                f"{state}: {p.get('error') or ''}".strip(": ")
                + (" — the .part file is kept and will resume" if state == "cancelled" else ""))
        if self._dl_stream:
            self._dl_stream.close()
            self._dl_stream = None
        if self.screen and hasattr(self.screen, "on_download_finished"):
            self.screen.on_download_finished(p)

    def _dl_ended(self) -> None:
        self._dl_stream = None

    async def _cancel_download(self) -> None:
        d = getattr(self, "_dl_current", None) or {}
        if not d.get("id"):
            return
        r = await ModelsApi().download_cancel_async(d["id"])
        failed(r, "cancel the download")


def _eta(seconds) -> str:
    s = int(seconds or 0)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{round(s / 60)} min"
    return f"{s / 3600:.1f} h"


_KEEP = []


def _proxy(fn):
    from pyodide.ffi import create_proxy

    p = create_proxy(fn)
    _KEEP.append(p)            # setInterval holds it for the page's lifetime
    return p
