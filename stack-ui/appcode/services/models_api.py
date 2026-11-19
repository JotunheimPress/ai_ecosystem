"""BFF: models on disk, the Kobold selection, VRAM, and HuggingFace/Civitai downloads.

Holds nothing between calls (pytincture re-executes it each time); state lives in the
plain modules it imports inside each method.

The rules from the old JSON routes carry over unchanged, because they live in the
services, not in the routes:

- files are named by **collection key + basename**, resolved by
  `inventory._safe_target()` — a request never carries a path;
- Civitai downloads take a **version id + filename** and the URL is looked up server
  side (`civitai.resolve_file`), so the browser can never hand the downloader a URL;
- one download at a time, refused rather than queued.

Transfer progress is followed over `GET /api/downloads/<id>/events` (service.py).
"""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend


def _err(exc: Exception) -> dict:
    return {"ok": False, "error": str(exc) or exc.__class__.__name__}


@backend_for_frontend
class ModelsApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    # --- language models (KoboldCPP) ------------------------------------------
    def llms(self) -> dict:
        """The GGUFs on disk, each with its layer count from the header."""
        from pathlib import Path

        from services import inventory, stackctl, vram

        k = inventory.kobold()
        models = []
        for m in k["models"]:
            meta = vram.gguf_meta(Path(m["path"]))
            models.append({**m, "arch": meta.get("arch"), "n_layers": meta.get("n_layers"),
                           "preset": k["presets"].get(m["name"])})
        return {"ok": True, **k, "models": models, "disk": stackctl.disk()}

    def select_llm(self, model: str, gpu_layers: int = -1) -> dict:
        """Pin the model KoboldCPP boots. Applies on its next start — never restarts.

        ``gpu_layers`` -1 means "not given": a remembered per-model value is reused.
        """
        from services import inventory

        try:
            layers = None if gpu_layers is None or int(gpu_layers) < 0 else int(gpu_layers)
            return {"ok": True, "kobold": inventory.select_kobold(model or "", layers)}
        except (inventory.InventoryError, TypeError, ValueError) as exc:
            return _err(exc)

    def llm_context(self) -> dict:
        """The context-window choices for the selected model, each with its KV-cache cost."""
        from services import inventory, vram

        k = inventory.kobold()
        meta = {}
        if k.get("active_present"):
            meta = vram.gguf_meta(inventory.KOBOLD_MODELS / k["active"])
        sizes = [{"size": n, "kv": vram.kv_cache_bytes(meta, n),
                  "past_training": bool(meta.get("train_ctx")) and n > meta["train_ctx"]}
                 for n in k["context_sizes"]]
        return {"ok": True, "model": k.get("active"), "context": k.get("context"),
                "default": k.get("context_default"), "running": k.get("running_context"),
                "train_ctx": meta.get("train_ctx"), "sizes": sizes}

    def set_llm_context(self, size: int = 0) -> dict:
        """Set KoboldCPP's context window (0 = its default). Applies on its next start."""
        from services import inventory

        try:
            value = int(size or 0)
            return {"ok": True, "kobold": inventory.set_kobold_context(value or None)}
        except (inventory.InventoryError, TypeError, ValueError) as exc:
            return _err(exc)

    # --- image models (ComfyUI) -----------------------------------------------
    def images(self) -> dict:
        from services import inventory, stackctl

        files = []
        for kind in inventory.IMAGE_KINDS:
            files.extend(inventory.comfy_files(kind))
        return {"ok": True, "files": files, "comfyui": inventory.comfyui(),
                "disk": stackctl.disk()}

    def delete(self, kind: str, name: str) -> dict:
        """Delete one model file. Refuses the selected and the loaded Kobold model."""
        from services import inventory

        try:
            return {"ok": True, **inventory.delete_model(kind or "", name or "")}
        except inventory.InventoryError as exc:
            return _err(exc)
        except OSError as exc:
            return {"ok": False, "error": f"delete failed: {exc}"}

    # --- VRAM -------------------------------------------------------------------
    def vram(self) -> dict:
        from pathlib import Path

        from services import inventory, vram

        k = inventory.kobold()
        models = [{"name": m["name"], "size": m["size"], "active": m["active"],
                   "n_layers": vram.gguf_meta(Path(m["path"])).get("n_layers")}
                  for m in k["models"]]
        return {"ok": True, "vram": vram.stats(), "comfy_up": vram.comfy_up(), "models": models,
                "checkpoints": inventory.checkpoints(), "active": k["active"],
                "gpu_layers": k["gpu_layers"]}

    def free_vram(self) -> dict:
        """Make ComfyUI release VRAM it is holding after a job — the fix for Kobold OOMing
        at startup while ComfyUI sits on 6 GB. No restart needed."""
        from services import vram

        try:
            return {"ok": True, "vram": vram.free_vram(), "comfy_up": True}
        except RuntimeError as exc:
            return _err(exc)

    def plan(self, model: str, checkpoint: str = "") -> dict:
        """How many layers of ``model`` fit alongside ``checkpoint``. An estimate."""
        from services import inventory, vram

        try:
            path = inventory._safe_target("kobold", model or "")
            ckpt = inventory._safe_target("checkpoint", checkpoint).stat().st_size \
                if checkpoint else 0
        except (inventory.InventoryError, OSError) as exc:
            return _err(exc)
        s = vram.stats()
        return {"ok": True, **vram.plan(path, path.stat().st_size, ckpt, s.get("total") or 0)}

    # --- HuggingFace ------------------------------------------------------------
    def hf_search(self, query: str, limit: int = 30) -> dict:
        from services import hub

        if not (query or "").strip():
            return {"ok": True, "results": []}
        try:
            return {"ok": True, "results": hub.search(query.strip(), kind="gguf",
                                                      limit=int(limit or 30))}
        except hub.HubError as exc:
            return _err(exc)

    def hf_files(self, repo: str) -> dict:
        """A repo's GGUFs with sizes, each carrying an advisory fit hint."""
        from services import hub, stackctl

        try:
            data = hub.files(repo or "", (".gguf",))
        except hub.HubError as exc:
            return _err(exc)
        total = hub.vram_total()
        free = stackctl.disk()["free"]
        for f in data["files"]:
            f["fit"] = hub.fit(f["size"], total, free)
        return {"ok": True, **data, "vram_total": total, "free_disk": free}

    # --- HuggingFace, image side -------------------------------------------------
    def hf_image_search(self, query: str, scope: str = "", limit: int = 30) -> dict:
        """Image repos. Unfiltered by default: the ComfyUI repacks (Comfy-Org/…, the
        text-encoder repos) carry neither a pipeline tag nor the safetensors library tag,
        so either filter hides exactly the repos that suit ComfyUI best. `scope` narrows
        on request: "comfyui" (the tag), or a pipeline tag."""
        from services import hub

        if not (query or "").strip():
            return {"ok": True, "results": []}
        if scope not in ("", "comfyui", "text-to-image", "image-to-image"):
            return {"ok": False, "error": f"unknown scope: {scope!r}"}
        kind = "comfyui" if scope == "comfyui" else ""
        task = scope if scope not in ("", "comfyui") else ""
        try:
            return {"ok": True, "results": hub.search(query.strip(), kind=kind,
                                                      limit=int(limit or 30), task=task)}
        except hub.HubError as exc:
            return _err(exc)

    def hf_image_files(self, repo: str) -> dict:
        """A repo's .safetensors, each with a suggested ComfyUI folder or a reason it
        can't be used on its own. No VRAM hint: for image models file size says little."""
        from services import hub, stackctl

        try:
            data = hub.files(repo or "", (".safetensors",))
        except hub.HubError as exc:
            return _err(exc)
        free = stackctl.disk()["free"]
        for f in data["files"]:
            f.update(hub.image_file(f["path"], data["repo"], data["diffusers"]))
            f["disk_ok"] = free > f["size"] * 1.05
        return {"ok": True, **data, "free_disk": free}

    def hf_image_inspect(self, repo: str, path: str) -> dict:
        """Read one file's tensor index (a byte-range request, never the weights) to say
        for certain which ComfyUI folder it belongs in."""
        from services import hub

        try:
            return {"ok": True, **hub.inspect(repo or "", path or "")}
        except hub.HubError as exc:
            return _err(exc)

    # --- Civitai ----------------------------------------------------------------
    def civitai_search(self, query: str, model_type: str = "Checkpoint",
                       base: str = "") -> dict:
        from services import civitai, stackctl

        if not (query or "").strip():
            return {"ok": True, "results": []}
        try:
            items = civitai.search(query.strip(), model_type=model_type or "Checkpoint",
                                   base=base or "")
        except civitai.CivitaiError as exc:
            return _err(exc)
        free = stackctl.disk()["free"]
        for m in items:
            for v in m["versions"]:
                for f in v["files"]:
                    f["disk_ok"] = free > f["size"] * 1.05
        return {"ok": True, "results": items, "free_disk": free}

    # --- downloads --------------------------------------------------------------
    def download_hf(self, repo: str, path: str, activate: bool = False,
                    kind: str = "kobold") -> dict:
        """`kind` is the destination collection: "kobold" for GGUFs, or one of
        inventory.IMAGE_KINDS. Activation only means something for Kobold."""
        from services import downloads, hub, inventory

        kind = kind or "kobold"
        if kind != "kobold":
            if kind not in inventory.IMAGE_KINDS:
                return {"ok": False, "error": f"unknown collection: {kind!r}"}
            # Re-check here: the browser's list said so, but the request is what counts.
            why = hub.image_file(path or "", repo or "").get("blocked")
            if why:
                return {"ok": False, "error": why}
            activate = False
        try:
            d = downloads.start_hf(repo or "", path or "", kind, bool(activate))
        except downloads.Busy as exc:
            return {**_err(exc), "busy": True}
        except (inventory.InventoryError, ValueError) as exc:
            return _err(exc)
        return {"ok": True, "download": d.public()}

    def download_civitai(self, version_id: int, name: str, kind: str) -> dict:
        from services import civitai, downloads, inventory

        try:
            d = downloads.start_civitai(version_id, name or "", kind or "", False)
        except downloads.Busy as exc:
            return {**_err(exc), "busy": True}
        except (inventory.InventoryError, civitai.CivitaiError, ValueError) as exc:
            return _err(exc)
        return {"ok": True, "download": d.public()}

    def download_state(self) -> dict:
        from services import downloads

        d = downloads.current()
        return {"ok": True, "download": d.public() if d else None}

    def download_cancel(self, job_id: str) -> dict:
        from services import downloads

        if not downloads.cancel(job_id or ""):
            return {"ok": False, "error": "not running"}
        return {"ok": True}
