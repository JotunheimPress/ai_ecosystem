"""Image models: what ComfyUI loads (checkpoints, LoRAs, VAEs), and Civitai search.

**Installed** — one row per file in the three collections this app can write, plus a
summary of every folder under ComfyUI/models (the ~70 GB walk is cached 30 s server-side).

**Find on Civitai** — Civitai keeps model → version → file, and picking the *version*
(SD 1.5 vs SDXL, v1.8 vs v2) is the real decision, so the file list shows the version and
its base model on every row rather than flattening them away. The browser sends a version
id and a filename; the download URL is looked up server-side.
"""
from __future__ import annotations

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
    message,
)

from services.models_api import ModelsApi
from ui.common import Listeners, by_id, container_of, esc, failed, fmt_bytes, link, search_url, spawn, toast
from ui.llms import _div

_KINDS = {"checkpoint": "Checkpoint", "lora": "LoRA", "vae": "VAE"}
_KIND_ICON = {"checkpoint": "mdi-cube-outline", "lora": "mdi-tune-variant", "vae": "mdi-palette-swatch-outline"}
# Civitai type -> our collection key
_CIVITAI = {"Checkpoint": "checkpoint", "LORA": "lora", "VAE": "vae"}
_BASES = ["", "SD 1.5", "SDXL 1.0", "Pony", "Illustrious", "Flux.1 D", "Flux.1 S", "SD 3.5"]


class ImagesView:
    def __init__(self, app, tab: str = "installed", **_):
        self.app = app
        self.listeners = Listeners()
        self.results: dict = {}
        self.model = None
        self._first_tab = tab

    def mount(self, root) -> None:
        page = _div(root, "su-page su-fill")
        _div(page, "su-page-head",
             '<h1>Image models</h1>'
             '<span class="su-dim" id="img-dir">Checkpoints, LoRAs and VAEs for ComfyUI</span>')
        host = _div(page, "su-tabs-host")
        self.tabs = TabWidget(TabWidgetConfig(tabs=[
            TabConfig(id="installed", title="Installed", icon="mdi-harddisk"),
            TabConfig(id="find", title="Find on Civitai", icon="mdi-magnify"),
        ], active=self._first_tab if self._first_tab in ("installed", "find") else "installed"),
            container=host)
        self._mount_installed(container_of(self.tabs.get_cell("installed")))
        self._mount_find(container_of(self.tabs.get_cell("find")))
        spawn(self.load(), "load images")

    def unmount(self) -> None:
        self.listeners.clear()

    def apply_args(self, tab: str = "", **_) -> None:
        if tab:
            self.tabs.set_active(tab)

    def on_download_finished(self, d: dict) -> None:
        if d.get("kind") in _KINDS:
            spawn(self.load(), "reload images")

    # --- installed -------------------------------------------------------------
    def _mount_installed(self, cell) -> None:
        col = _div(cell, "su-col")
        _div(col, "su-status", '<span class="su-dim">loading…</span>').id = "img-summary"
        self.tb = Toolbar(ToolbarConfig(label="Installed image models", items=[
            ToolbarButton(id="page", label="Source", icon="mdi-open-in-new", disabled=True),
            ToolbarButton(id="delete", label="Delete", icon="mdi-delete-outline",
                          variant="danger", disabled=True),
            ToolbarSpacer(),
            ToolbarButton(id="refresh", label="Refresh", icon="mdi-refresh", show_label=False),
        ]), container=_div(col, "su-toolbar-host"))
        self.tb.on_click(lambda p: self._action((p or {}).get("id")))
        self.table = DataTable(DataTableConfig(
            columns=[
                ColumnConfig(id="kind_text", header="Kind", icon_by="icon", width="14%"),
                ColumnConfig(id="name", header="File", width="56%"),
                ColumnConfig(id="size_text", header="Size", sort_by="size", align="right",
                             width="14%"),
                ColumnConfig(id="source", header="Source", width="16%"),
            ],
            id_field="rid", selection="single", sort_by="kind_text",
            filterable=True, filter_placeholder="Filter files…",
            empty_text="No checkpoints, LoRAs or VAEs yet — find some on Civitai.",
            context_actions=[
                TableAction(id="page", label="Source page", icon="mdi-open-in-new"),
                TableAction(separator=True),
                TableAction(id="delete", label="Delete…", icon="mdi-delete-outline", danger=True),
            ],
        ), container=_div(col, "su-table-host"))
        self.table.on_select(lambda _p: self._sync())
        self.table.on_action(lambda p: self._action((p or {}).get("action"), (p or {}).get("id")))

    async def load(self) -> None:
        self.table.set_busy(True)
        r = await ModelsApi().images_async()
        self.table.set_busy(False)
        if failed(r, "list the image models"):
            return
        c = r.get("comfyui") or {}
        by_id("img-dir").textContent = f"Checkpoints, LoRAs and VAEs for ComfyUI · {c.get('dir')}"
        chips = "".join(
            f'<span class="su-chip"><b>{esc(k["kind"])}</b> {int(k["count"])} · '
            f'{fmt_bytes(k["size"])}</span>' for k in c.get("kinds") or [])
        d = r.get("disk") or {}
        by_id("img-summary").innerHTML = (
            f'<span>{fmt_bytes(c.get("total"))} in ComfyUI/models</span>{chips}'
            f'<span class="su-dim">{fmt_bytes(d.get("free"))} free on disk</span>')
        self.table.set_rows([{
            **f,
            "rid": f'{f["kind"]}/{f["name"]}',
            "kind_text": _KINDS.get(f["kind"], f["kind"]),
            "icon": _KIND_ICON.get(f["kind"], "mdi-file-outline"),
            "size_text": fmt_bytes(f.get("size")),
            "source": "Civitai" if "civitai" in (f.get("url") or "") else
                      ("HuggingFace" if f.get("url") else "—"),
        } for f in r.get("files") or []])
        self._sync()

    def _sync(self) -> None:
        none = not self.table.get_selected_rows()
        self.tb.set_disabled("page", none)
        self.tb.set_disabled("delete", none)

    def _action(self, action, rid=None) -> None:
        if action == "refresh":
            spawn(self.load(), "load images")
            return
        row = self.table.get_row(rid) if rid else ((self.table.get_selected_rows() or [None])[0])
        if not row:
            return
        if action == "page":
            js.window.open(row.get("url") or search_url(row["kind"], row["name"]), "_blank",
                           "noopener,noreferrer")
            if not row.get("url"):
                toast("No recorded source for this file — opened a Civitai search.", "info")
        elif action == "delete":
            spawn(self._delete(row), "delete image model")

    async def _delete(self, row: dict) -> None:
        if not await message.confirm(
                f"Delete {row['name']}? This frees {fmt_bytes(row.get('size'))} and cannot be "
                "undone.", title=f"Delete {row['kind_text']}", ok_text="Delete", danger=True):
            return
        r = await ModelsApi().delete_async(row["kind"], row["name"])
        if failed(r, f"delete {row['name']}"):
            return
        toast(f"Deleted {row['name']} — freed {fmt_bytes(r.get('freed'))}", "success")
        await self.load()

    # --- find ------------------------------------------------------------------
    def _mount_find(self, cell) -> None:
        col = _div(cell, "su-col")
        types = "".join(f'<option value="{t}">{label}</option>' for t, label in
                        (("Checkpoint", "Checkpoints"), ("LORA", "LoRAs"), ("VAE", "VAEs")))
        bases = "".join(f'<option value="{esc(b)}">{esc(b or "Any base model")}</option>'
                        for b in _BASES)
        _div(col, "su-searchbar",
             '<i class="mdi mdi-magnify"></i>'
             f'<select id="cv-type">{types}</select>'
             f'<select id="cv-base">{bases}</select>'
             '<input id="cv-q" type="search" autocomplete="off" '
             'placeholder="Search Civitai — absolutereality, juggernaut, detail tweaker…">'
             '<button class="su-btn su-primary" id="cv-go">Search</button>')
        self.listeners.on(by_id("cv-go"), "click", lambda _e: self._search())
        self.listeners.on(by_id("cv-q"), "keydown",
                          lambda e: self._search() if e.key == "Enter" else None)

        split = _div(col, "su-split")
        left = _div(split, "su-split-left")
        self.models = DataTable(DataTableConfig(
            columns=[
                ColumnConfig(id="name", header="Model", icon_by="icon", width="50%"),
                ColumnConfig(id="creator", header="Creator", width="22%"),
                ColumnConfig(id="dl_text", header="Downloads", sort_by="downloads",
                             align="right", width="18%"),
                ColumnConfig(id="nsfw_text", header="", width="10%"),
            ],
            id_field="id", selection="single", sort_by="downloads", sort_dir="desc",
            empty_text="Search for a model to see results.",
        ), container=_div(left, "su-table-host"))
        self.models.on_select(lambda p: self._pick((p or {}).get("id")))

        right = _div(split, "su-split-right")
        _div(right, "su-status",
             '<span class="su-dim">Pick a model to see its versions and files.</span>').id = "cv-model"
        self.ftb = Toolbar(ToolbarConfig(label="Files", items=[
            ToolbarButton(id="dl", label="Download", icon="mdi-download", variant="primary",
                          disabled=True),
            ToolbarButton(id="vpage", label="Version page", icon="mdi-open-in-new",
                          disabled=True),
        ]), container=_div(right, "su-toolbar-host"))
        self.ftb.on_click(lambda p: self._file_action((p or {}).get("id")))
        self.files = DataTable(DataTableConfig(
            columns=[
                ColumnConfig(id="version", header="Version", width="26%"),
                ColumnConfig(id="base", header="Base", width="16%"),
                ColumnConfig(id="name", header="File", icon_by="icon", width="42%"),
                ColumnConfig(id="size_text", header="Size", sort_by="size", align="right",
                             width="16%"),
            ],
            id_field="fid", selection="single", sortable=False,
            empty_text="No files yet.",
            context_actions=[
                TableAction(id="dl", label="Download", icon="mdi-download"),
                TableAction(id="vpage", label="Version page", icon="mdi-open-in-new"),
            ],
        ), container=_div(right, "su-table-host"))
        self.files.on_select(lambda _p: self._sync_files())
        self.files.on_action(lambda p: self._file_action((p or {}).get("action"),
                                                         (p or {}).get("id")))

    def _search(self) -> None:
        q = str(by_id("cv-q").value or "").strip()
        if q:
            spawn(self._run_search(q, str(by_id("cv-type").value), str(by_id("cv-base").value)),
                  "civitai search")

    async def _run_search(self, q: str, model_type: str, base: str) -> None:
        self.models.set_busy(True)
        r = await ModelsApi().civitai_search_async(q, model_type, base)
        self.models.set_busy(False)
        # Civitai's own error text is surfaced as is: "503: search is temporarily
        # overloaded" tells you to wait, a bare 503 sends you debugging this client.
        if failed(r, "search Civitai"):
            return
        self.results = {str(m["id"]): m for m in r.get("results") or []}
        self.models.set_empty_text("Nothing found.")
        self.models.set_rows([{
            "id": str(m["id"]), "name": m.get("name") or "", "creator": m.get("creator") or "",
            "downloads": m.get("downloads") or 0,
            "dl_text": f"{int(m['downloads']):,}" if m.get("downloads") else "—",
            "nsfw_text": "nsfw" if m.get("nsfw") else "",
            "icon": _KIND_ICON.get(m.get("kind"), "mdi-cube-outline"),
        } for m in self.results.values()])
        self.files.set_rows([])
        self.model = None

    def _pick(self, model_id) -> None:
        m = self.results.get(str(model_id or ""))
        if not m:
            return
        self.model = m
        page = f"https://civitai.com/models/{int(m['id'])}"
        by_id("cv-model").innerHTML = (
            f'<b>{esc(m.get("name"))}</b> {link(page, "model page")} '
            '<span class="su-dim">licence, trigger words and examples are on its page</span>')
        rows = []
        for v in m.get("versions") or []:
            for f in v.get("files") or []:
                rows.append({
                    "fid": f'{v["id"]}/{f["name"]}',
                    "version_id": v["id"], "version": v.get("name") or f'version {v["id"]}',
                    "base": v.get("base") or "", "name": f["name"], "size": f.get("size") or 0,
                    "size_text": fmt_bytes(f.get("size")),
                    "icon": "mdi-star" if f.get("primary") else "mdi-file-outline",
                    "icon_title": "primary file" if f.get("primary") else "",
                    "disk_ok": f.get("disk_ok"),
                })
        self.files.set_rows(rows)
        self._sync_files()

    def _sync_files(self) -> None:
        rows = self.files.get_selected_rows()
        self.ftb.set_disabled("vpage", not rows)
        self.ftb.set_disabled("dl", not rows or rows[0].get("disk_ok") is False)

    def _file_action(self, action, fid=None) -> None:
        row = self.files.get_row(fid) if fid else ((self.files.get_selected_rows() or [None])[0])
        if not row or not self.model:
            return
        if action == "vpage":
            js.window.open(f"https://civitai.com/models/{int(self.model['id'])}"
                           f"?modelVersionId={int(row['version_id'])}", "_blank",
                           "noopener,noreferrer")
        elif action == "dl":
            if row.get("disk_ok") is False:
                toast("Not enough free disk for that file.", "error")
                return
            spawn(self._download(row), "civitai download")

    async def _download(self, row: dict) -> None:
        kind = _CIVITAI.get(self.model.get("type") or "", self.model.get("kind") or "checkpoint")
        if not await message.confirm(
                f"Download {row['name']} ({fmt_bytes(row['size'])}) — {row['version']}, "
                f"{row['base'] or 'unknown base'} — into ComfyUI's {_KINDS.get(kind, kind)}s?",
                title="Download model", ok_text="Download"):
            return
        r = await ModelsApi().download_civitai_async(int(row["version_id"]), row["name"], kind)
        # Civitai gates many files behind an account now (CIVITAI_TOKEN); the error says so.
        if failed(r, "start the download"):
            return
        self.app.watch_download(r["download"])
