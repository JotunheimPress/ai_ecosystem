"""System screens: container logs, ComfyUI custom-node pins, and the doctor."""
from __future__ import annotations

import js

from wapyt import (
    ColumnConfig,
    DataTable,
    DataTableConfig,
    Toolbar,
    ToolbarButton,
    ToolbarConfig,
    ToolbarSpacer,
    ToolbarText,
)

from services.stack_api import StackApi
from ui.common import Listeners, Stream, by_id, esc, failed, spawn
from ui.llms import _div

_MAX_LOG_CHARS = 400_000          # keep the page responsive on a chatty container


class LogsView:
    """Follow one app's container log (`stack logs <app>` → `podman logs -f`).

    Leaving the screen, switching app or pressing Stop closes the stream, and the server
    kills the `podman logs -f` process group when it does — it never exits on its own.
    """

    def __init__(self, app, app_name: str = "", **kwargs):
        self.app = app
        self.listeners = Listeners()
        self.stream = None
        self.want = kwargs.get("app") or app_name
        self.following = ""

    def mount(self, root) -> None:
        page = _div(root, "su-page su-fill")
        _div(page, "su-page-head", '<h1>Logs</h1><span class="su-dim">podman logs -f, live</span>')
        _div(page, "su-searchbar",
             '<i class="mdi mdi-text-long"></i>'
             '<select id="lg-app"></select>'
             '<button class="su-btn su-primary" id="lg-follow"><i class="mdi mdi-play"></i>Follow</button>'
             '<button class="su-btn" id="lg-stop" disabled><i class="mdi mdi-stop"></i>Stop</button>'
             '<button class="su-btn su-ghost" id="lg-clear">Clear</button>'
             '<label class="su-check"><input type="checkbox" id="lg-tail" checked>Stick to the end</label>'
             '<span class="su-dim" id="lg-state"></span>')
        _div(page, "su-log-host", '<pre class="su-log" id="lg-out"></pre>')
        self.listeners.on(by_id("lg-follow"), "click", lambda _e: self._start(str(by_id("lg-app").value)))
        self.listeners.on(by_id("lg-stop"), "click", lambda _e: self._stop("stopped"))
        self.listeners.on(by_id("lg-clear"), "click", lambda _e: setattr(by_id("lg-out"), "textContent", ""))
        self._fill_apps(self.app.overview)
        if self.want:
            self._start(self.want)

    def unmount(self) -> None:
        self._stop("")
        self.listeners.clear()

    def apply_args(self, app: str = "", **_) -> None:
        if app:
            self._start(app)

    def on_overview(self, ov: dict) -> None:
        self._fill_apps(ov)

    def _fill_apps(self, ov: dict) -> None:
        sel = by_id("lg-app")
        if not sel:
            return
        apps = (ov or {}).get("apps") or []
        current = self.following or self.want or str(sel.value or "")
        names = [a["name"] for a in apps]
        if [str(o.value) for o in sel.options] == names:
            return
        sel.innerHTML = "".join(
            f'<option value="{esc(a["name"])}"{" selected" if a["name"] == current else ""}>'
            f'{esc(a.get("label") or a["name"])} ({esc(a["name"])}){"" if a.get("up") else " — down"}'
            '</option>' for a in apps)

    def _start(self, name: str) -> None:
        if not name:
            return
        self._stop("")
        self.want = name
        sel = by_id("lg-app")
        if sel:
            sel.value = name
        out = by_id("lg-out")
        out.textContent = ""
        self.following = name
        by_id("lg-follow").disabled = True
        by_id("lg-stop").disabled = False
        by_id("lg-state").textContent = f"following {name}…"

        def on_event(ev: dict) -> None:
            if ev.get("type") == "line":
                text = out.textContent + (ev.get("text") or "") + "\n"
                if len(text) > _MAX_LOG_CHARS:
                    text = text[-_MAX_LOG_CHARS:]
                out.textContent = text
                if by_id("lg-tail").checked:
                    out.scrollTop = out.scrollHeight
            elif ev.get("type") == "done":
                out.textContent += "\n[stream ended — the container stopped]\n"
                self._stop("ended")

        self.stream = Stream(f"/api/logs/{js.encodeURIComponent(name)}/events", on_event,
                             on_end=lambda: self._ended(name))

    def _ended(self, name: str) -> None:
        self.stream = None
        if self.following == name:
            self._stop("closed — is the container running?")

    def _stop(self, state: str) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        self.following = ""
        if by_id("lg-follow"):
            by_id("lg-follow").disabled = False
            by_id("lg-stop").disabled = True
            by_id("lg-state").textContent = state


_NODE_STATE = {
    "on_pin": ("mdi-check-circle", "on pin"),
    "off_pin": ("mdi-alert-circle-outline", "off pin"),
    "missing": ("mdi-close-circle-outline", "missing"),
    "not_git": ("mdi-help-circle-outline", "not a checkout"),
    "unreadable": ("mdi-help-circle-outline", "unreadable"),
}


class NodesView:
    """ComfyUI custom-node pins, read from `stack`'s own CUSTOM_NODES registry — so this
    screen and `stack doctor` cannot disagree about which commit is right. The fix is
    `stack install comfyui` (idempotent: clone, fetch, re-checkout)."""

    def __init__(self, app, **_):
        self.app = app
        self.fixable = False

    def mount(self, root) -> None:
        page = _div(root, "su-page su-fill")
        _div(page, "su-page-head",
             '<h1>Custom nodes</h1><span class="su-dim" id="nd-note">ComfyUI nodes pinned by stack</span>')
        _div(page, "su-status",
             '<span class="su-dim"><i class="mdi mdi-information-outline"></i> Re-pinning does not '
             'install Python dependencies — a node that arrives this way may still need a ComfyUI '
             'image rebuild (deps live in comfyui/Containerfile).</span>')
        self.tb = Toolbar(ToolbarConfig(label="Custom nodes", items=[
            ToolbarButton(id="sync", label="Sync to pins", icon="mdi-source-branch-sync",
                          variant="primary", hidden=True,
                          tooltip="stack install comfyui — clones or re-checks-out the pinned nodes"),
            ToolbarSpacer(),
            ToolbarText(id="summary", text=""),
            ToolbarButton(id="refresh", label="Refresh", icon="mdi-refresh", show_label=False),
        ]), container=_div(page, "su-toolbar-host"))
        self.tb.on_click(self._on_toolbar)
        self.table = DataTable(DataTableConfig(
            columns=[
                ColumnConfig(id="name", header="Node", width="40%"),
                ColumnConfig(id="state_text", header="State", icon_by="icon",
                             cell_class_by="state_class", width="20%"),
                ColumnConfig(id="commit", header="Commit", width="40%"),
            ],
            id_field="name", selection="none", sort_by="name",
            empty_text="No custom nodes registered.",
        ), container=_div(page, "su-table-host"))
        spawn(self.load(), "load nodes")

    def unmount(self) -> None:
        pass

    async def load(self) -> None:
        self.table.set_busy(True)
        r = await StackApi().nodes_async()
        self.table.set_busy(False)
        if failed(r, "read the node pins"):
            return
        rows = []
        for n in r.get("nodes") or []:
            icon, text = _NODE_STATE.get(n.get("state"), ("mdi-help-circle-outline", n.get("state")))
            pin, head = (n.get("pin") or "")[:7], (n.get("head") or "")[:7]
            rows.append({
                "name": n.get("name"), "state_text": text, "icon": icon,
                "state_class": "su-good" if n.get("state") == "on_pin" else "su-warn",
                "commit": f"{head} → {pin}" if head and n.get("state") == "off_pin" else pin,
            })
        self.table.set_rows(rows)
        self.tb.set_text("summary", "drifted from the pinned commits" if r.get("drift")
                         else f"all on pin · {r.get('note') or ''}")
        # `not_git` is excluded: stack reports those and leaves them alone, so a button
        # offering to fix them would be a lie.
        self.fixable = any(n.get("state") in ("off_pin", "missing") for n in r.get("nodes") or [])
        self.tb.set_hidden("sync", not self.fixable)
        self.tb.set_disabled("sync", self.app.busy)

    def on_busy(self, busy: bool) -> None:
        self.tb.set_disabled("sync", busy)
        if not busy:
            spawn(self.load(), "reload nodes")

    def _on_toolbar(self, p) -> None:
        item = (p or {}).get("id")
        if item == "refresh":
            spawn(self.load(), "load nodes")
        elif item == "sync":
            self.app.act("install_comfyui", "",
                         confirm="Run `stack install comfyui`? It clones or re-checks-out the "
                                 "pinned nodes. Python deps for new nodes still need a ComfyUI "
                                 "image rebuild.")


class DoctorView:
    """`stack doctor`, on demand — it shells out to podman several times, so it is never
    polled."""

    def __init__(self, app, **_):
        self.app = app
        self.listeners = Listeners()

    def mount(self, root) -> None:
        page = _div(root, "su-page")
        _div(page, "su-page-head",
             '<h1>Doctor</h1><span class="su-dim">read-only prerequisite and install check</span>')
        _div(page, "su-panel",
             '<div class="su-panel-head"><h2>stack doctor</h2>'
             '<button class="su-btn su-primary" id="dr-run"><i class="mdi mdi-stethoscope"></i>Run</button></div>'
             '<p class="su-dim">Three checks always fail from inside this container and are not '
             'real: <code>nvidia-smi</code> and the CDI spec need host GPU tooling, and the '
             'systemd-unit check looks for a file under <code>$HOME</code>. Run '
             '<code>stack doctor</code> in a terminal for those.</p>'
             '<div id="dr-out"></div>')
        self.listeners.on(by_id("dr-run"), "click", lambda _e: spawn(self._run(), "doctor"))

    def unmount(self) -> None:
        self.listeners.clear()

    async def _run(self) -> None:
        btn, out = by_id("dr-run"), by_id("dr-out")
        btn.disabled = True
        out.innerHTML = '<p class="su-dim">running…</p>'
        try:
            r = await StackApi().doctor_async()
        finally:
            btn.disabled = False
        if not isinstance(r, dict) or not r.get("ok"):
            out.innerHTML = f'<p class="su-bad">doctor failed: {esc((r or {}).get("error"))}</p>'
            return
        lines = "".join(f'<div class="su-check-line warn"><i class="mdi mdi-alert"></i>{esc(w)}</div>'
                        for w in r.get("warnings") or [])
        lines += "".join(f'<div class="su-check-line ok"><i class="mdi mdi-check"></i>{esc(o)}</div>'
                         for o in r.get("ok") or [])
        out.innerHTML = (lines + f'<details><summary>raw output ({int(r.get("duration_ms") or 0)} ms)'
                         f'</summary><pre class="su-raw">{esc(r.get("raw"))}</pre></details>')
