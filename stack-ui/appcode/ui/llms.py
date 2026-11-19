"""Language models: the GGUFs KoboldCPP can load, HuggingFace search to get more, and the
models Fortuna (decisions) can serve.

Three tabs.

**Installed** — every .gguf in koboldcpp/models with its size, architecture and layer
count (read from the file header), the one selected to boot and the one actually loaded.
Use writes model.env and never restarts on its own: a restart drops what is loaded and
costs 20–30 s, so it is offered, not done. The Context window box sets KOBOLDCPP_CONTEXT
the same way, each size labelled with its KV-cache cost for the selected model. Delete refuses the selected and the loaded
model server-side.

**Find on HuggingFace** — search (GGUF repos only), pick a repo, pick a quant. Each file
carries an advisory fit band for this card; the band is suppressed, not guessed, when
VRAM is unknown (ComfyUI down). Every repo links to its page — the licence, prompt format
and model card live there, not here.

**Fortuna** — the pinned files from fortuna/models.table, with their sha256 state; see
ui/fortuna_models.py.
"""
from __future__ import annotations

from urllib.parse import quote

import js

from wapyt import (
    ColumnConfig,
    DataTable,
    DataTableConfig,
    TabConfig,
    TableAction,
    TabWidget,
    TabWidgetConfig,
    Toolbar,
    ToolbarButton,
    ToolbarConfig,
    ToolbarSpacer,
    ToolbarText,
    message,
)

from services.models_api import ModelsApi
from ui.fortuna_models import FortunaModels
from ui.common import (
    Listeners,
    by_id,
    container_of,
    esc,
    failed,
    fmt_bytes,
    link,
    search_url,
    spawn,
    toast,
)

_FIT_ICON = {"fits": "mdi-check-circle", "tight": "mdi-alert-circle-outline",
             "partial": "mdi-progress-alert", "unknown": "mdi-help-circle-outline"}


def _div(parent, cls: str, html: str = ""):
    el = js.document.createElement("div")
    el.className = cls
    if html:
        el.innerHTML = html
    parent.appendChild(el)
    return el


def _quant(name: str) -> str:
    """The quant tag in a GGUF filename (Q4_K_M, IQ3_XS, F16 …), or ''."""
    import re

    m = re.search(r"(?i)[-_.]((?:I?Q\d[A-Z0-9_]*)|BF16|F16|F32)(?=[-_.]|\.gguf$)", name)
    return m.group(1).upper() if m else ""


class LlmsView:
    def __init__(self, app, tab: str = "installed", **_):
        self.app = app
        self.listeners = Listeners()
        self.state: dict = {}
        self.repo: str = ""
        self.files: dict = {}
        self._first_tab = tab

    # --- mount -----------------------------------------------------------------
    def mount(self, root) -> None:
        page = _div(root, "su-page su-fill")
        _div(page, "su-page-head",
             '<h1>Language models</h1>'
             '<span class="su-dim" id="llm-dir">GGUF files for KoboldCPP</span>')
        host = _div(page, "su-tabs-host")
        self.tabs = TabWidget(TabWidgetConfig(tabs=[
            TabConfig(id="installed", title="Installed", icon="mdi-harddisk"),
            TabConfig(id="find", title="Find on HuggingFace", icon="mdi-magnify"),
            TabConfig(id="fortuna", title="Fortuna", icon="mdi-scale-balance"),
        ], active=self._first_tab if self._first_tab in ("installed", "find", "fortuna")
            else "installed"),
            container=host)
        self._mount_installed(container_of(self.tabs.get_cell("installed")))
        self._mount_find(container_of(self.tabs.get_cell("find")))
        self.fortuna = FortunaModels(self.app)
        self.fortuna.mount(container_of(self.tabs.get_cell("fortuna")))
        spawn(self.load(), "load llms")
        spawn(self.fortuna.load(), "load fortuna")

    def unmount(self) -> None:
        self.listeners.clear()
        self.fortuna.unmount()

    def apply_args(self, tab: str = "", **_) -> None:
        if tab:
            self.tabs.set_active(tab)

    # --- installed -------------------------------------------------------------
    def _mount_installed(self, cell) -> None:
        col = _div(cell, "su-col")
        _div(col, "su-status", '<span class="su-dim">loading…</span>').id = "llm-status"
        _div(col, "su-searchbar su-ctxbar",
             '<i class="mdi mdi-text-box-multiple-outline"></i>'
             '<label for="llm-ctx">Context window</label>'
             '<select id="llm-ctx" disabled><option>loading…</option></select>'
             '<span class="su-dim" id="llm-ctx-note"></span>')
        self.listeners.on(by_id("llm-ctx"), "change", lambda _e: spawn(self._set_context(), "context"))
        self.tb = Toolbar(ToolbarConfig(label="Installed models", items=[
            ToolbarButton(id="use", label="Use for KoboldCPP", icon="mdi-check-bold",
                          variant="primary", disabled=True,
                          tooltip="Boot this model next time (writes model.env; no restart)"),
            ToolbarButton(id="layers", label="Use with GPU layers…", icon="mdi-layers-outline",
                          disabled=True,
                          tooltip="Select it with a specific number of layers on the GPU"),
            ToolbarButton(id="page", label="Source", icon="mdi-open-in-new", disabled=True),
            ToolbarButton(id="delete", label="Delete", icon="mdi-delete-outline",
                          variant="danger", disabled=True),
            ToolbarSpacer(),
            ToolbarButton(id="restart", label="Restart KoboldCPP", icon="mdi-restart",
                          variant="accent", hidden=True,
                          tooltip="Load the selected model now"),
            ToolbarButton(id="refresh", label="Refresh", icon="mdi-refresh", show_label=False),
        ]), container=_div(col, "su-toolbar-host"))
        self.tb.on_click(lambda p: self._installed_action((p or {}).get("id")))

        self.table = DataTable(DataTableConfig(
            columns=[
                ColumnConfig(id="name", header="Model", icon_by="icon", width="44%"),
                ColumnConfig(id="quant", header="Quant", width="9%"),
                ColumnConfig(id="size_text", header="Size", sort_by="size", align="right",
                             width="10%"),
                ColumnConfig(id="arch", header="Architecture", width="12%"),
                ColumnConfig(id="layers_text", header="Layers", sort_by="n_layers",
                             align="right", width="8%"),
                ColumnConfig(id="gpu_text", header="GPU layers", align="right", width="9%"),
                ColumnConfig(id="source", header="Source", width="8%"),
            ],
            id_field="name",
            selection="single",
            sort_by="name",
            filterable=True,
            filter_placeholder="Filter models…",
            empty_text="No .gguf files in koboldcpp/models — find one on HuggingFace.",
            row_class_by="row_class",
            context_actions=[
                TableAction(id="use", label="Use for KoboldCPP", icon="mdi-check-bold"),
                TableAction(id="layers", label="Use with GPU layers…", icon="mdi-layers-outline"),
                TableAction(id="page", label="Source page", icon="mdi-open-in-new"),
                TableAction(separator=True),
                TableAction(id="delete", label="Delete…", icon="mdi-delete-outline", danger=True),
            ],
        ), container=_div(col, "su-table-host"))
        self.table.on_select(lambda _p: self._sync_toolbar())
        self.table.on_activate(lambda p: self._installed_action("use", (p or {}).get("id")))
        self.table.on_action(lambda p: self._installed_action((p or {}).get("action"),
                                                              (p or {}).get("id")))

    async def load(self) -> None:
        self.table.set_busy(True)
        r = await ModelsApi().llms_async()
        self.table.set_busy(False)
        if failed(r, "list the models"):
            return
        self.state = r
        by_id("llm-dir").textContent = (f"GGUF files for KoboldCPP · {r.get('dir')} — and "
                                         "Fortuna's, on its own tab")
        rows = []
        for m in r.get("models") or []:
            gpu = m.get("preset")
            if m.get("active") and r.get("gpu_layers"):
                gpu = r["gpu_layers"]
            if m.get("running"):
                icon, title = "mdi-play-circle", "loaded in KoboldCPP right now"
            elif m.get("active"):
                icon, title = "mdi-pin", "selected — KoboldCPP boots this one"
            else:
                icon, title = "mdi-file-outline", ""
            rows.append({
                **m,
                "icon": icon, "icon_title": title,
                "quant": _quant(m["name"]),
                "size_text": fmt_bytes(m.get("size")),
                "arch": m.get("arch") or "",
                "layers_text": str(m["n_layers"]) if m.get("n_layers") else "—",
                "gpu_text": str(gpu) if gpu not in (None, "") else "default",
                "source": "HuggingFace" if m.get("url") else "—",
                "row_class": "su-row-active" if m.get("active") else "",
            })
        self.table.set_rows(rows)
        self._render_status()
        self._sync_toolbar()
        await self._load_context()

    # --- context window ----------------------------------------------------------
    async def _load_context(self) -> None:
        r = await ModelsApi().llm_context_async()
        sel = by_id("llm-ctx")
        if not sel or failed(r, "read the context window"):
            return
        self.ctx = r
        current = r.get("context") or 0
        default = r.get("default") or 8192
        opts = [(0, f"Default — {default // 1024}K ({default:,} tokens, KoboldCPP's own)")]
        for x in r.get("sizes") or []:
            n = x["size"]
            label = f"{n // 1024}K — {n:,} tokens"
            if x.get("kv"):
                label += f" · ~{fmt_bytes(x['kv'])} KV cache"
            if x.get("past_training"):
                label += " · past what the model was trained on"
            opts.append((n, label))
        sel.innerHTML = "".join(
            f'<option value="{n}"{" selected" if n == current else ""}>{esc(t)}</option>'
            for n, t in opts)
        sel.disabled = bool(self.app.busy)
        self._context_note()

    def _context_note(self) -> None:
        r = getattr(self, "ctx", None) or {}
        running, want = r.get("running"), r.get("context") or r.get("default")
        bits = []
        if running:
            bits.append(f"running at {running:,}")
        if running and want and running != want:
            bits.append(f"restart to switch to {want:,}")
        bits.append("KV cache estimate is for the selected model, on top of its weights")
        by_id("llm-ctx-note").textContent = " · ".join(bits)

    async def _set_context(self) -> None:
        sel = by_id("llm-ctx")
        size = int(str(sel.value) or 0)
        r = await ModelsApi().set_llm_context_async(size)
        if failed(r, "set the context window"):
            await self._load_context()
            return
        k = r.get("kobold") or {}
        await self.load()
        shown = f"{size:,} tokens" if size else "KoboldCPP's default"
        if "context" in (k.get("restart_for") or []):
            if await message.confirm(
                    f"KoboldCPP will use {shown}. It is running at {k.get('running_context'):,} "
                    "now.\n\nRestart it now? Loading takes 20–30 seconds.",
                    title="Restart KoboldCPP?", ok_text="Restart now", cancel_text="Later"):
                self.app.act("restart", "koboldcpp")
        else:
            toast(f"Context window set to {shown}"
                  + ("." if k.get("running_context") else " — applies when KoboldCPP starts."), "success")

    def _render_status(self) -> None:
        r = self.state
        sel, running = r.get("active"), r.get("running")
        parts = []
        if sel and not r.get("active_present"):
            parts.append(f'<span class="su-bad"><i class="mdi mdi-alert"></i> Selected model '
                         f'is missing from models/: {esc(sel)}</span>')
        elif sel:
            layers = f", {esc(r['gpu_layers'])} GPU layers" if r.get("gpu_layers") else ""
            parts.append(f'<span><i class="mdi mdi-pin"></i> Boots <b>{esc(sel)}</b> '
                         f'<span class="su-dim">({esc(r.get("source"))}{layers})</span></span>')
        else:
            parts.append('<span class="su-dim">No model selected.</span>')
        parts.append(f'<span><i class="mdi mdi-play-circle"></i> Loaded <b>{esc(running)}</b></span>'
                     if running else '<span class="su-dim">KoboldCPP is not running.</span>')
        d = r.get("disk") or {}
        if d:
            parts.append(f'<span class="su-dim">{fmt_bytes(d.get("free"))} free on disk</span>')
        if r.get("restart_needed"):
            what = {"model": "the selected model", "context": "the new context window"}
            why = " and ".join(what[w] for w in r.get("restart_for") or ["model"])
            parts.append(f'<span class="su-warn">Restart KoboldCPP to load {why}.</span>')
        by_id("llm-status").innerHTML = " ".join(parts)
        self.tb.set_hidden("restart", not r.get("restart_needed"))
        self.tb.set_disabled("restart", self.app.busy)

    def _selected(self):
        rows = self.table.get_selected_rows()
        return rows[0] if rows else None

    def _sync_toolbar(self) -> None:
        row = self._selected()
        for item in ("use", "layers", "page", "delete"):
            self.tb.set_disabled(item, row is None)
        if row is not None and row.get("active") and not self.state.get("restart_needed"):
            self.tb.set_disabled("use", True)

    def on_busy(self, busy: bool) -> None:
        self.fortuna.on_busy(busy)
        self.tb.set_disabled("restart", busy)
        sel = by_id("llm-ctx")
        if sel:
            sel.disabled = bool(busy)
        if not busy:
            spawn(self.load(), "reload llms")     # a restart just finished: show what loaded

    def on_overview(self, _ov) -> None:
        pass

    def on_download_finished(self, d: dict) -> None:
        if d.get("kind") == "kobold":
            spawn(self.load(), "reload llms")

    def _installed_action(self, action, name=None) -> None:
        if action == "refresh":
            spawn(self.load(), "load llms")
            return
        if action == "restart":
            self.app.act("restart", "koboldcpp")
            return
        row = self.table.get_row(name) if name else self._selected()
        if not row:
            return
        if action == "use":
            spawn(self._use(row["name"], -1), "use model")
        elif action == "layers":
            spawn(self._layers(row), "gpu layers")
        elif action == "page":
            url = row.get("url") or search_url("kobold", row["name"])
            js.window.open(url, "_blank", "noopener,noreferrer")
            if not row.get("url"):
                toast("No recorded source for this file — opened a HuggingFace search.", "info")
        elif action == "delete":
            spawn(self._delete(row), "delete model")

    async def _use(self, name: str, layers: int) -> None:
        r = await ModelsApi().select_llm_async(name, layers)
        if failed(r, f"select {name}"):
            return
        k = r.get("kobold") or {}
        await self.load()
        if k.get("restart_needed"):
            if await message.confirm(
                    f"{name} is selected. KoboldCPP still has {k.get('running')} loaded.\n\n"
                    "Restart it now? Loading takes 20–30 seconds.",
                    title="Restart KoboldCPP?", ok_text="Restart now", cancel_text="Later"):
                self.app.act("restart", "koboldcpp")
        else:
            toast(f"{name} will load on KoboldCPP's next start.", "success")

    async def _layers(self, row: dict) -> None:
        n = row.get("n_layers")
        current = row.get("gpu_text") if row.get("gpu_text") != "default" else ""
        hint = f" It has {n} layers;" if n else ""
        value = await message.prompt(
            f"Use {row['name']} with how many layers on the GPU?{hint} lower the number if "
            "it will not fit next to ComfyUI. The VRAM planner can suggest one.",
            title="GPU layers", value=str(current or n or ""), placeholder="e.g. 28")
        if value is None:
            return
        value = str(value).strip()
        if not value.isdigit():
            toast("GPU layers must be a whole number.", "error")
            return
        await self._use(row["name"], int(value))

    async def _delete(self, row: dict) -> None:
        if not await message.confirm(
                f"Delete {row['name']}? This frees {fmt_bytes(row.get('size'))} and cannot be "
                "undone.", title="Delete model", ok_text="Delete", danger=True):
            return
        r = await ModelsApi().delete_async("kobold", row["name"])
        # The server refuses the selected and the loaded model; its reason says what to
        # do first, so it is shown as is.
        if failed(r, f"delete {row['name']}"):
            return
        toast(f"Deleted {row['name']} — freed {fmt_bytes(r.get('freed'))}", "success")
        await self.load()

    # --- find ------------------------------------------------------------------
    def _mount_find(self, cell) -> None:
        col = _div(cell, "su-col")
        bar = _div(col, "su-searchbar",
                   '<i class="mdi mdi-magnify"></i>'
                   '<input id="hf-q" type="search" autocomplete="off" '
                   'placeholder="Search GGUF repos — qwen3 8b, mistral nemo, gemma…">'
                   '<button class="su-btn su-primary" id="hf-go">Search</button>')
        self.listeners.on(by_id("hf-go"), "click", lambda _e: self._search())
        self.listeners.on(by_id("hf-q"), "keydown",
                          lambda e: self._search() if e.key == "Enter" else None)
        del bar

        split = _div(col, "su-split")
        left = _div(split, "su-split-left")
        self.repos = DataTable(DataTableConfig(
            columns=[
                ColumnConfig(id="id", header="Repository", icon_by="icon", width="66%"),
                ColumnConfig(id="dl_text", header="Downloads", sort_by="downloads",
                             align="right", width="20%"),
                ColumnConfig(id="likes", header="Likes", align="right", width="14%"),
            ],
            id_field="id", selection="single", sort_by="downloads", sort_dir="desc",
            empty_text="Search for a model to see GGUF repos.",
        ), container=_div(left, "su-table-host"))
        self.repos.on_select(lambda p: self._pick_repo((p or {}).get("id")))

        right = _div(split, "su-split-right")
        _div(right, "su-status", '<span class="su-dim">Pick a repository to see its files.</span>'
             ).id = "hf-repo"
        self.ftb = Toolbar(ToolbarConfig(label="Files", items=[
            ToolbarButton(id="dl", label="Download", icon="mdi-download", disabled=True),
            ToolbarButton(id="dl_use", label="Download and use", icon="mdi-download-circle",
                          variant="primary", disabled=True,
                          tooltip="Select it for KoboldCPP once it lands (no restart)"),
            ToolbarSpacer(),
            ToolbarText(id="fit_note", text=""),
        ]), container=_div(right, "su-toolbar-host"))
        self.ftb.on_click(lambda p: self._file_action((p or {}).get("id")))
        self.file_table = DataTable(DataTableConfig(
            columns=[
                ColumnConfig(id="name", header="File", icon_by="fit_icon", width="52%"),
                ColumnConfig(id="quant", header="Quant", width="13%"),
                ColumnConfig(id="size_text", header="Size", sort_by="size", align="right",
                             width="14%"),
                ColumnConfig(id="band", header="Fit", cell_class_by="band_class", width="21%"),
            ],
            id_field="path", selection="single", sort_by="size",
            empty_text="No files yet.",
            context_actions=[
                TableAction(id="dl", label="Download", icon="mdi-download"),
                TableAction(id="dl_use", label="Download and use", icon="mdi-download-circle"),
            ],
        ), container=_div(right, "su-table-host"))
        self.file_table.on_select(lambda _p: self._sync_files())
        self.file_table.on_action(lambda p: self._file_action((p or {}).get("action"),
                                                              (p or {}).get("id")))

    def _search(self) -> None:
        q = str(by_id("hf-q").value or "").strip()
        if q:
            spawn(self._run_search(q), "hf search")

    async def _run_search(self, q: str) -> None:
        self.repos.set_busy(True)
        r = await ModelsApi().hf_search_async(q)
        self.repos.set_busy(False)
        if failed(r, "search HuggingFace"):
            return
        rows = [{
            **x,
            "icon": "mdi-lock-outline" if x.get("gated") else "mdi-source-repository",
            "icon_title": "gated — needs HF_TOKEN and accepting the licence" if x.get("gated") else "",
            "dl_text": f"{int(x['downloads']):,}" if x.get("downloads") else "—",
            "likes": x.get("likes") or 0,
        } for x in r.get("results") or []]
        self.repos.set_empty_text("Nothing found.")
        self.repos.set_rows(rows)

    def _pick_repo(self, repo) -> None:
        if repo and repo != self.repo:
            self.repo = repo
            spawn(self._load_files(repo), "hf files")

    async def _load_files(self, repo: str) -> None:
        page = "https://huggingface.co/" + "/".join(quote(p) for p in repo.split("/"))
        by_id("hf-repo").innerHTML = (f'<b>{esc(repo)}</b> {link(page, "model card")} '
                                      '<span class="su-dim">licence, prompt format and details '
                                      'are on its page</span>')
        self.file_table.set_rows([])
        self.file_table.set_busy(True)
        r = await ModelsApi().hf_files_async(repo)
        self.file_table.set_busy(False)
        if repo != self.repo:
            return                                   # a later pick won the race
        if failed(r, f"list {repo}"):
            return
        self.files = r
        rows = []
        for f in r.get("files") or []:
            fit = f.get("fit") or {}
            band = fit.get("band") or "unknown"
            rows.append({
                **f,
                "quant": _quant(f["name"]),
                "size_text": fmt_bytes(f.get("size")),
                "band": band if band != "unknown" else "—",
                "band_class": f"su-band-{band}",
                "fit_icon": _FIT_ICON.get(band, _FIT_ICON["unknown"]),
                "fit_icon_title": fit.get("note") or "",
                "disk_ok": fit.get("disk_ok"),
            })
        self.file_table.set_empty_text("No .gguf files in this repo.")
        self.file_table.set_rows(rows)
        self.ftb.set_text("fit_note", "" if r.get("vram_total") else
                          "VRAM unknown (ComfyUI is down) — no fit hints")
        self._sync_files()

    def _sync_files(self) -> None:
        rows = self.file_table.get_selected_rows()
        ok = bool(rows) and rows[0].get("disk_ok") is not False
        self.ftb.set_disabled("dl", not ok)
        self.ftb.set_disabled("dl_use", not ok)

    def _file_action(self, action, path=None) -> None:
        if action not in ("dl", "dl_use"):
            return
        row = self.file_table.get_row(path) if path else (
            (self.file_table.get_selected_rows() or [None])[0])
        if not row:
            return
        if row.get("disk_ok") is False:
            toast("Not enough free disk for that file.", "error")
            return
        spawn(self._download(row, action == "dl_use"), "download")

    async def _download(self, row: dict, activate: bool) -> None:
        extra = ("\n\nIt will be selected for KoboldCPP when it finishes (applies on the next "
                 "restart).") if activate else ""
        if not await message.confirm(
                f"Download {row['name']} ({fmt_bytes(row.get('size'))}) from {self.repo}?{extra}",
                title="Download model", ok_text="Download"):
            return
        r = await ModelsApi().download_hf_async(self.repo, row["path"], activate)
        if failed(r, "start the download"):
            return
        self.app.watch_download(r["download"])
