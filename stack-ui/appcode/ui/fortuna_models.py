"""Language models → Fortuna: the model files Fortuna (decisions) can serve.

Not a directory listing: Fortuna serves only the files in ai-stack/fortuna/models.table, each
pinned to one HuggingFace commit with its sha256 and its own calibration temperatures. So the
table lists every known file, downloaded or not, with its **integrity**:

- *verified* — its sha256 matched the table, and it hasn't changed since (start.sh's stamp);
- *not checked yet* — present, never hashed (copied in by hand, or touched since);
- *failed sha256* — hashed and wrong; start.sh refuses it, and so does Use here;
- *partial* / *not downloaded*.

Download and Verify are stack actions (`stack fortuna-model fetch|verify NAME`), streamed in the
console like any other: the recipe owns the pinned URL and the hash, and this page only names a
row. Use writes MODEL= into fortuna.env and never restarts; a restart is offered when Fortuna has
another model loaded. Delete refuses the model Fortuna boots and the one it has loaded.

GPU layers… writes NGL= into fortuna.env (0 = CPU only, which frees its VRAM; empty = all layers,
the default) and offers a restart when Fortuna is running.
"""
from __future__ import annotations

import js

from wapyt import (
    ColumnConfig,
    DataTable,
    DataTableConfig,
    TableAction,
    Toolbar,
    ToolbarButton,
    ToolbarConfig,
    ToolbarSpacer,
    message,
)

from services.models_api import ModelsApi
from ui.common import Listeners, by_id, esc, failed, fmt_bytes, spawn, toast

_INTEGRITY = {
    "verified": ("mdi-shield-check", "verified"),
    "unchecked": ("mdi-shield-outline", "not checked yet"),
    "mismatch": ("mdi-shield-alert", "failed sha256"),
    "partial": ("mdi-download-off-outline", "partial download"),
    "absent": ("mdi-cloud-download-outline", "not downloaded"),
}


def _div(parent, cls: str, html: str = ""):
    el = js.document.createElement("div")
    el.className = cls
    if html:
        el.innerHTML = html
    parent.appendChild(el)
    return el


class FortunaModels:
    """The Fortuna tab's content. Owned by LlmsView, which forwards busy changes to it."""

    def __init__(self, app):
        self.app = app
        self.listeners = Listeners()
        self.state: dict = {}

    def mount(self, cell) -> None:
        col = _div(cell, "su-col")
        _div(col, "su-status", '<span class="su-dim">loading…</span>').id = "ft-status"
        self.tb = Toolbar(ToolbarConfig(label="Fortuna models", items=[
            ToolbarButton(id="use", label="Use for Fortuna", icon="mdi-check-bold",
                          variant="primary", disabled=True,
                          tooltip="Boot this model next time (writes fortuna.env; no restart)"),
            ToolbarButton(id="fetch", label="Download", icon="mdi-download", disabled=True,
                          tooltip="Download at the pinned commit and check its sha256"),
            ToolbarButton(id="verify", label="Verify", icon="mdi-shield-check-outline",
                          disabled=True, tooltip="Hash the file now and compare it with the table"),
            ToolbarButton(id="page", label="Model card", icon="mdi-open-in-new", disabled=True),
            ToolbarButton(id="delete", label="Delete", icon="mdi-delete-outline",
                          variant="danger", disabled=True),
            ToolbarButton(id="layers", label="GPU layers…", icon="mdi-layers-outline",
                          tooltip="How much of Fortuna runs on the GPU (0 = CPU only, frees its "
                                  "VRAM); applies on its next start"),
            ToolbarSpacer(),
            ToolbarButton(id="restart", label="Restart Fortuna", icon="mdi-restart",
                          variant="accent", hidden=True, tooltip="Load the selected model now"),
            ToolbarButton(id="refresh", label="Refresh", icon="mdi-refresh", show_label=False),
        ]), container=_div(col, "su-toolbar-host"))
        self.tb.on_click(lambda p: self._action((p or {}).get("id")))

        self.table = DataTable(DataTableConfig(
            columns=[
                ColumnConfig(id="name", header="Model", icon_by="icon", width="14%"),
                ColumnConfig(id="file", header="File", width="25%"),
                ColumnConfig(id="size_text", header="Size", sort_by="bytes", align="right",
                             width="8%"),
                ColumnConfig(id="calibration", header="Calibration", width="19%"),
                ColumnConfig(id="integrity_text", header="Integrity", icon_by="integrity_icon",
                             width="20%"),
                ColumnConfig(id="default_text", header="", width="14%"),
            ],
            id_field="name",
            selection="single",
            empty_text="No models in ai-stack/fortuna/models.table.",
            row_class_by="row_class",
            context_actions=[
                TableAction(id="use", label="Use for Fortuna", icon="mdi-check-bold"),
                TableAction(id="fetch", label="Download", icon="mdi-download"),
                TableAction(id="verify", label="Verify sha256", icon="mdi-shield-check-outline"),
                TableAction(id="page", label="Model card", icon="mdi-open-in-new"),
                TableAction(separator=True),
                TableAction(id="delete", label="Delete…", icon="mdi-delete-outline", danger=True),
            ],
        ), container=_div(col, "su-table-host"))
        self.table.on_select(lambda _p: self._sync_toolbar())
        self.table.on_activate(lambda p: self._action("use", (p or {}).get("id")))
        self.table.on_action(lambda p: self._action((p or {}).get("action"), (p or {}).get("id")))

    def unmount(self) -> None:
        self.listeners.clear()

    async def load(self) -> None:
        self.table.set_busy(True)
        r = await ModelsApi().fortuna_models_async()
        self.table.set_busy(False)
        if failed(r, "list Fortuna's models"):
            return
        self.state = r
        rows = []
        for m in r.get("models") or []:
            if m.get("running"):
                icon, title = "mdi-play-circle", "loaded in Fortuna right now"
            elif m.get("selected"):
                icon, title = "mdi-pin", "selected — Fortuna boots this one"
            else:
                icon, title = "mdi-file-outline", ""
            ii, it = _INTEGRITY.get(m.get("integrity"), ("mdi-help-circle-outline", "unknown"))
            if m.get("integrity") == "partial":
                it += f" ({fmt_bytes(m.get('part_bytes'))} of {fmt_bytes(m.get('bytes'))})"
            rows.append({
                **m, "icon": icon, "icon_title": title,
                "size_text": fmt_bytes(m.get("bytes")),
                "calibration": f"T {m.get('temperature')} · knockout {m.get('knockout')}",
                "integrity_text": it, "integrity_icon": ii,
                "default_text": f"default on {r.get('gpu')}" if m.get("default") else "",
                "row_class": "su-row-active" if m.get("selected") else "",
            })
        self.table.set_rows(rows)
        self._render_status()
        self._sync_toolbar()

    def _render_status(self) -> None:
        r = self.state
        sel, running = r.get("selected"), r.get("running")
        parts = []
        if sel and not r.get("selected_known"):
            parts.append(f'<span class="su-bad"><i class="mdi mdi-alert"></i> fortuna.env names '
                         f'<b>{esc(sel)}</b>, which models.table does not know — Fortuna will not '
                         'start. Pick one below.</span>')
        elif sel:
            ii, it = _INTEGRITY.get(r.get("selected_integrity"), ("mdi-help-circle-outline", ""))
            cls = {"verified": "su-good", "mismatch": "su-bad", "absent": "su-bad",
                   "partial": "su-bad"}.get(r.get("selected_integrity"), "su-warn")
            parts.append(f'<span><i class="mdi mdi-pin"></i> Boots <b>{esc(sel)}</b> '
                         f'<span class="su-dim">({esc(r.get("source"))})</span> '
                         f'<span class="{cls}"><i class="mdi {ii}"></i>{esc(it)}</span></span>')
        else:
            parts.append('<span class="su-dim">No model selected.</span>')
        parts.append(f'<span><i class="mdi mdi-play-circle"></i> Loaded <b>{esc(running)}</b></span>'
                     if running else '<span class="su-dim">Fortuna is not running.</span>')
        parts.append(self._ngl_html())
        d = r.get("disk") or {}
        if d:
            parts.append(f'<span class="su-dim">{fmt_bytes(d.get("free"))} free on disk</span>')
        if r.get("repo"):
            parts.append(f'<span class="su-dim">{esc(r["repo"])} @ {esc((r.get("rev") or "")[:12])}'
                         '</span>')
        if r.get("restart_needed"):
            parts.append('<span class="su-warn">Restart Fortuna to load the selected model.</span>')
        by_id("ft-status").innerHTML = " ".join(parts)
        self.tb.set_hidden("restart", not r.get("restart_needed"))
        self.tb.set_disabled("restart", self.app.busy)

    def _ngl_html(self) -> str:
        r = self.state
        if r.get("ngl_invalid"):
            return ('<span class="su-bad"><i class="mdi mdi-alert"></i> fortuna.env has an NGL= '
                    'that is not a whole number — Fortuna will not start. Set GPU layers.</span>')
        n, total = r.get("ngl_effective"), r.get("n_layers")
        if r.get("gpu") == "none":
            what = "CPU only (no GPU)"
        elif n == 0:
            what = "CPU only"
        elif r.get("ngl") is None or (total and n >= total):
            what = "all layers on the GPU"
        else:
            what = f"{n} of {total} layers on the GPU" if total else f"{n} layers on the GPU"
        return f'<span><i class="mdi mdi-layers-outline"></i> {esc(what)}</span>'

    def _selected(self):
        rows = self.table.get_selected_rows()
        return rows[0] if rows else None

    def _sync_toolbar(self) -> None:
        row = self._selected()
        busy = bool(self.app.busy)
        present = bool(row and row.get("present"))
        self.tb.set_disabled("use", not present or row.get("integrity") == "mismatch"
                             or (row.get("selected") and not self.state.get("restart_needed")))
        self.tb.set_disabled("fetch", busy or row is None or present)
        self.tb.set_disabled("verify", busy or not present)
        self.tb.set_disabled("page", row is None or not row.get("url"))
        self.tb.set_disabled("delete", not present or row.get("selected") or row.get("running"))

    def on_busy(self, busy: bool) -> None:
        self.tb.set_disabled("restart", busy)
        self._sync_toolbar()
        if not busy:
            spawn(self.load(), "reload fortuna")   # a download or verify just finished

    # --- actions -------------------------------------------------------------------
    def _action(self, action, name=None) -> None:
        if action == "refresh":
            spawn(self.load(), "load fortuna")
            return
        if action == "restart":
            self.app.act("restart", "fortuna")
            return
        if action == "layers":
            spawn(self._layers(), "fortuna gpu layers")
            return
        row = self.table.get_row(name) if name else self._selected()
        if not row:
            return
        if action == "use":
            spawn(self._use(row), "use fortuna model")
        elif action == "fetch":
            spawn(self._fetch(row), "fetch fortuna model")
        elif action == "verify":
            if not row.get("present"):
                toast(f"{row['name']} is not downloaded.", "warning")
                return
            self.app.act("fortuna_verify", row["name"])
        elif action == "page" and row.get("url"):
            js.window.open(row["url"], "_blank", "noopener,noreferrer")
        elif action == "delete":
            spawn(self._delete(row), "delete fortuna model")

    async def _use(self, row: dict) -> None:
        if row.get("integrity") == "unchecked" and not await message.confirm(
                f"{row['name']} has not been checked against its sha256 yet. Fortuna hashes it on "
                "its next start and refuses it if it does not match.\n\nUse it anyway?",
                title="Not verified", ok_text="Use it", cancel_text="Cancel"):
            return
        r = await ModelsApi().select_fortuna_async(row["name"])
        if failed(r, f"select {row['name']}"):
            return
        f = r.get("fortuna") or {}
        await self.load()
        if f.get("restart_needed"):
            if await message.confirm(
                    f"{row['name']} is selected. Fortuna still has {f.get('running')} loaded.\n\n"
                    "Restart it now? Decisions are unavailable for the few seconds it takes.",
                    title="Restart Fortuna?", ok_text="Restart now", cancel_text="Later"):
                self.app.act("restart", "fortuna")
        else:
            toast(f"{row['name']} will load on Fortuna's next start.", "success")

    async def _layers(self) -> None:
        r = self.state
        total = r.get("n_layers")
        hint = f" {r.get('selected')} has {total} layers." if total else ""
        current = "" if r.get("ngl") is None else str(r["ngl"])
        value = await message.prompt(
            f"How many of Fortuna's layers go on the GPU?{hint} 0 runs it on the CPU only and "
            "frees its VRAM for ComfyUI, at some latency per question. Leave it empty for all "
            "layers (the default).", title="Fortuna GPU layers", value=current,
            placeholder="empty = all, 0 = CPU only")
        if value is None:
            return
        value = str(value).strip()
        if value and not value.isdigit():
            toast("GPU layers must be a whole number, or empty for all of them.", "error")
            return
        r = await ModelsApi().set_fortuna_ngl_async(int(value) if value else None)
        if failed(r, "set Fortuna's GPU layers"):
            return
        f = r.get("fortuna") or {}
        await self.load()
        if f.get("running"):
            if await message.confirm(
                    "Saved. Fortuna applies it on its next start.\n\nRestart it now? Decisions "
                    "are unavailable for the few seconds it takes.",
                    title="Restart Fortuna?", ok_text="Restart now", cancel_text="Later"):
                self.app.act("restart", "fortuna")
        else:
            toast("Saved — Fortuna uses it on its next start.", "success")

    async def _fetch(self, row: dict) -> None:
        free = (self.state.get("disk") or {}).get("free") or 0
        need = int(row.get("bytes") or 0) - int(row.get("part_bytes") or 0)
        if free and free < need * 1.05:
            toast(f"Not enough disk: {fmt_bytes(need)} needed, {fmt_bytes(free)} free.", "error")
            return
        resume = " It resumes the partial download." if row.get("integrity") == "partial" else ""
        self.app.act("fortuna_fetch", row["name"],
                     confirm=f"Download {row['file']} ({fmt_bytes(row.get('bytes'))}) from "
                             f"{self.state.get('repo')} at its pinned commit, then check its "
                             f"sha256?{resume} Progress shows in the console.")

    async def _delete(self, row: dict) -> None:
        if not await message.confirm(
                f"Delete {row['file']}? This frees {fmt_bytes(row.get('bytes'))}; downloading it "
                "again takes a while.", title="Delete model", ok_text="Delete", danger=True):
            return
        r = await ModelsApi().delete_async("fortuna", row["file"])
        if failed(r, f"delete {row['file']}"):
            return
        toast(f"Deleted {row['file']} — freed {fmt_bytes(r.get('freed'))}", "success")
        await self.load()
