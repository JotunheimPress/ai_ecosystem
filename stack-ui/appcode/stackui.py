"""AI Ecosystem — the stack's control dashboard. Runs in the browser (Pyodide + wapyt).

Shape: a header toolbar (whether the backends run, Start/Stop, the separate start-at-boot
toggle, and the workspace switch),
a sidebar of screens, and the screen itself. Two workspaces:

* **Infrastructure** (the landing page, ``#/infrastructure``): the router, one card per
  capability (text, image and sound, decision) and the GPU. Its sidebar screens are language
  models, image and audio models, the VRAM planner, logs, custom-node pins and the doctor.
* **Frontends** (``#/frontends``): the installed apps that use the infrastructure.

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
from ui.about import AboutView
from ui.common import (Listeners, Stream, attr, by_id, closest, container_of, failed,
                       fmt_bytes, link, spawn, toast)
from ui.dashboard import FrontendsView, InfrastructureView
from ui.images import ImagesView
from ui.llms import LlmsView
from ui.system import DoctorView, LogsView, NodesView
from ui.themes import apply as apply_theme
from ui.themes import current as current_theme
from ui.themes import picker_html
from ui.vramview import VramView

APP_ENTRYPOINT = "StackUI"

POLL_MS = 10_000

SCREENS = {
    "infrastructure": InfrastructureView,
    "frontends": FrontendsView,
    "llms": LlmsView,
    "images": ImagesView,
    "vram": VramView,
    "logs": LogsView,
    "nodes": NodesView,
    "doctor": DoctorView,
    "about": AboutView,
}
# Old bookmarks: the single dashboard became the Infrastructure workspace.
ALIASES = {"dashboard": "infrastructure", "": "infrastructure"}

# `stack install` runs its prerequisite check first, and the CDI spec check (nvidia-ctk)
# cannot pass from inside this container — so it exits 1 even when the node work succeeded.
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
        self.release: dict = {}        # StackApi.latest_release(), once per load

        self._load_css()
        # Before any widget is built, so the first paint is already in the chosen colours.
        apply_theme(self, current_theme())
        js.document.title = "AI Ecosystem"

        self.toolbar = self.add_toolbar("header", ToolbarConfig(label="Stack", items=[
            ToolbarText(id="brand", text="AI Ecosystem"),
            ToolbarSeparator(),
            # What runs now and what starts at boot are separate settings (stack boot).
            ToolbarText(id="state", text="Backends …"),
            ToolbarButton(id="up", label="Start backends", icon="mdi-play",
                          tooltip="Start the router and every backend now (stack up); "
                                  "start at boot is unchanged"),
            ToolbarButton(id="downbk", label="Stop backends", icon="mdi-stop", variant="danger",
                          tooltip="Stop the router and every backend now; frontends keep running "
                                  "and start at boot is unchanged"),
            ToolbarSeparator(),
            ToolbarButton(id="boot", label="Start at boot", icon="mdi-restart", toggle=True,
                          tooltip="Whether the backends start when the machine boots "
                                  "(stack boot on|off). Starts or stops nothing now."),
            ToolbarSeparator(),
            ToolbarButton(id="ws-infra", label="Infrastructure", icon="mdi-server-network",
                          group="ws", active=True, tooltip="The shared backends and the router"),
            ToolbarButton(id="ws-front", label="Frontends", icon="mdi-apps", group="ws",
                          tooltip="The installed apps that use them"),
            ToolbarSpacer(),
            # Shown by check_release when a newer release is out; opens About.
            ToolbarButton(id="update", label="Update", icon="mdi-arrow-up-circle",
                          variant="accent", hidden=True),
            ToolbarText(id="updated", text=""),
            ToolbarButton(id="refresh", icon="mdi-refresh", tooltip="Refresh now",
                          show_label=False, label="Refresh"),
        ]))
        self.toolbar.on_click(self._on_toolbar)
        # wapyt gives toolbar text no id; tag the brand and the running state for the CSS
        # (the brand's logo tile, the bold state).
        texts = container_of(self.get_cell("header")).querySelectorAll(".wapyt-toolbar-text")
        for el, role in zip(texts, ("brand", "state")):
            el.setAttribute("data-su", role)
        self._brand = texts[0] if texts.length else None

        self.sidebar = self.add_sidebar("nav", SidebarConfig(
            collapse_button=True,
            active="infrastructure",
            items=[
                SidebarHeading(label="Infrastructure"),
                SidebarItem(id="infrastructure", label="Overview", icon="mdi-server-network"),
                SidebarItem(id="llms", label="Language models",
                            icon="mdi-text-box-multiple-outline",
                            tooltip="Installed GGUFs for KoboldCPP, and HuggingFace search"),
                SidebarItem(id="images", label="Image and audio",
                            icon="mdi-image-multiple-outline",
                            tooltip="ComfyUI checkpoints, LoRAs and VAEs, and Civitai search"),
                SidebarItem(id="vram", label="VRAM planner", icon="mdi-memory"),
                SidebarHeading(label="Frontends"),
                SidebarItem(id="frontends", label="Installed apps", icon="mdi-apps"),
                SidebarHeading(label="System"),
                SidebarItem(id="logs", label="Logs", icon="mdi-text-long"),
                SidebarItem(id="nodes", label="Custom nodes", icon="mdi-puzzle-outline"),
                SidebarItem(id="doctor", label="Doctor", icon="mdi-stethoscope"),
                SidebarItem(id="about", label="About", icon="mdi-information-outline"),
            ],
        ))
        self.sidebar.on_select(lambda p: self.go((p or {}).get("id") or "infrastructure"))
        # Theme swatches under the sidebar, as in Whimsy's rail.
        nav = container_of(self.get_cell("nav"))
        nav.classList.add("su-nav-host")
        foot = js.document.createElement("div")
        foot.innerHTML = picker_html()
        nav.appendChild(foot.firstElementChild)
        self._listeners.on(nav, "click", self._on_swatch)
        apply_theme(self, current_theme())          # marks the active swatch

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
        spawn(self.check_release(), "release check")
        self._poll_proxy = js.setInterval(_proxy(self._tick), POLL_MS)

    def _on_swatch(self, ev) -> None:
        sw = closest(ev, ".su-swatch")
        if sw:
            apply_theme(self, attr(sw, "data-theme"), remember=True)

    def _load_css(self) -> None:
        if by_id("su-css"):
            return
        el = js.document.createElement("link")
        el.id = "su-css"
        el.rel = "stylesheet"
        el.href = "/static/stackui.css?v=4"
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
        raw = ALIASES.get(raw, raw)
        screen_id = raw if raw in SCREENS else "infrastructure"
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
        self.toolbar.set_active("ws-front" if screen_id == "frontends" else "ws-infra", True)

    # --- polling ---------------------------------------------------------------
    def _tick(self, *_):
        # Not over a running action: its output stream is the live view, and a refresh
        # mid-action only fights the disabled-button state.
        if not self.busy and not js.document.hidden:
            spawn(self.refresh(), "refresh")

    def _show_release(self, rel) -> None:
        """The AI Ecosystem release beside the brand (CSS draws data-version), and the
        checkout's commit in its tooltip. Nothing from an older stack that doesn't say."""
        el = self._brand
        if not el:
            return
        if not isinstance(rel, dict) or not rel.get("version"):
            el.removeAttribute("data-version")
            el.removeAttribute("title")
            return
        el.setAttribute("data-version", f"v{rel['version']}")
        tip = f"AI Ecosystem {rel['version']}"
        if rel.get("commit"):
            tip += f" (ai-stack {rel['commit']}{', modified' if rel.get('modified') else ''})"
        el.setAttribute("title", tip)

    async def refresh(self) -> None:
        ov = await StackApi().overview_async()
        if not isinstance(ov, dict) or not ov.get("ok"):
            self.toolbar.set_text("state", "stack status failed")
            self.toolbar.set_text("updated", (ov or {}).get("error", "") if isinstance(ov, dict) else "")
            return
        self.overview = ov
        backends = [a for a in ov.get("apps") or [] if a.get("group") == "backends"]
        up = sum(1 for a in backends if a.get("up"))
        if not backends:
            state = "Backends unknown"
        elif up == len(backends):
            state = "Backends running"
        elif up == 0:
            state = "Backends stopped"
        else:
            state = f"Backends: {up} of {len(backends)} up"
        self.toolbar.set_text("state", state)
        self._show_release(ov.get("release"))
        boot = ov.get("boot")
        self.toolbar.set_active("boot", boot == "ON")
        self.toolbar.set_text("boot", f"Start at boot: {'on' if boot == 'ON' else 'off'}")
        self.toolbar.set_icon("boot", "mdi-restart" if boot == "ON" else "mdi-restart-off")
        # No systemd unit: there is nothing to toggle (stack install service).
        self.toolbar.set_hidden("boot", boot is None)
        self.toolbar.set_text("updated", f"updated {js.Date.new().toLocaleTimeString()} · "
                                         f"{ov.get('duration_ms')} ms")
        # A job started in another tab (or before a reload) still owns the slot: follow it
        # rather than letting this page think it is idle.
        if ov.get("busy") and ov.get("job") and not self._action_stream:
            self._follow(ov["job"])
        if self.screen and hasattr(self.screen, "on_overview"):
            self.screen.on_overview(ov)

    async def check_release(self, force: bool = False) -> None:
        """Ask the server whether a newer release is out (services.release_check: cached
        there, off with STACK_UPDATE_CHECK=off) and, if so, show the toolbar badge. Quiet on
        any failure; About says what went wrong."""
        if force:
            self.release = {}
            if hasattr(self.screen, "on_release"):
                self.screen.on_release(self.release)
        r = await StackApi().latest_release_async(force)
        self.release = dict(r) if isinstance(r, dict) and r.get("ok") else {
            "checked": False, "reason": "unreachable", "error": (r or {}).get("error", "")}
        newer = bool(self.release.get("newer"))
        if newer:
            self.toolbar.set_text("update", f"Update {self.release.get('latest')}")
            self.toolbar.set_tooltip("update", f"AI Ecosystem {self.release.get('latest')} is out: see About")
        self.toolbar.set_hidden("update", not newer)
        if hasattr(self.screen, "on_release"):
            self.screen.on_release(self.release)

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
        elif item == "boot":
            want = bool((p or {}).get("active"))
            if self.busy:
                self.toolbar.set_active("boot", not want)      # undo the click's toggle
                toast("Another action is still running.", "warning")
                return
            # Saved at once, no dialog: it changes nothing until the next boot.
            self.act("boot_on" if want else "boot_off")
        elif item == "up":
            self.act("up")
        elif item == "downbk":
            self.act("down", "backends",
                     confirm="Stop the router and every backend? Frontends keep running but lose "
                             "text, images and decisions until the backends start again. "
                             "Start at boot is unchanged.")
        elif item == "ws-infra":
            self.go("infrastructure")
        elif item == "ws-front":
            self.go("frontends")
        elif item == "update":
            self.go("about")

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
        for item in ("boot", "up", "downbk"):
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
