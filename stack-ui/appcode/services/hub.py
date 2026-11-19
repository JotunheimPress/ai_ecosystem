"""HuggingFace model search — and the fit hints that make a choice possible.

huggingface.co can't be iframed, so browsing means our own UI over their JSON API. No key
is needed for public repos; `HF_TOKEN` is picked up when set, for gated ones.

**No `huggingface_hub` dependency.** The earlier lean was to take it for resume and
caching. It isn't worth it here: the download loop has to be hand-written anyway to report
byte-level progress (`services/downloads.py`), the library's cache would put files in a
HF-shaped cache dir rather than the models dir the backends actually read, and this is a
control-plane container where a small dependency surface is worth more than convenience.
The API used here is three URLs.
"""
from __future__ import annotations

import json
import re
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://huggingface.co/api"
UA = "stack-ui/0.3 (+local control dashboard)"
TIMEOUT = 20

# What a quant needs on top of its own weights: KV cache and context at the sizes this
# machine runs. A rule of thumb, not a measurement — the bands below are advisory.
KV_OVERHEAD = int(1.5 * 1024**3)
# ComfyUI shares the card. An SD 1.5 checkpoint is ~2 GB resident, which is the pairing
# both INSTALL guides recommend, so a model that leaves less than this is flagged.
COMPANION_RESERVE = 2 * 1024**3


class HubError(RuntimeError):
    pass


def _get(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    token = os.environ.get("HF_TOKEN", "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise HubError("access denied — a gated repo needs HF_TOKEN set")
        raise HubError(f"huggingface returned {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HubError(f"cannot reach huggingface: {e}")
    except ValueError:
        raise HubError("huggingface returned something that isn't JSON")


def search(query: str, kind: str = "gguf", limit: int = 25, task: str = "") -> list[dict]:
    """Search models. `filter=gguf` is what makes this usable for Kobold — it returns the
    quant repos directly rather than the base weights. `task` is HuggingFace's
    pipeline_tag ("text-to-image"), which is how the image screen narrows the search."""
    q = {
        "search": query, "sort": "downloads", "direction": "-1",
        "limit": max(1, min(limit, 50)),
    }
    if kind:
        q["filter"] = kind
    if task:
        q["pipeline_tag"] = task
    data = _get(f"{API}/models?{urllib.parse.urlencode(q)}")
    if not isinstance(data, list):
        raise HubError("unexpected search response")
    return [{
        "id": m.get("id", ""),
        "downloads": m.get("downloads"),
        "likes": m.get("likes"),
        "gated": bool(m.get("gated")),
        "updated": m.get("lastModified"),
    } for m in data if m.get("id")]


def files(repo: str, exts: tuple[str, ...] = (".gguf",)) -> dict:
    """List a repo's downloadable files, with sizes.

    The sizes are the entire point: choosing a GGUF *is* choosing a quant that fits 12 GB,
    and that decision is impossible without them.
    """
    if not repo or repo.count("/") != 1 or any(c in repo for c in "?&#"):
        raise HubError(f"not a repo id: {repo!r}")
    data = _get(f"{API}/models/{urllib.parse.quote(repo)}?blobs=true")
    if not isinstance(data, dict):
        raise HubError("unexpected repo response")
    out = []
    for s in data.get("siblings") or []:
        name = s.get("rfilename") or ""
        if name.endswith(exts) and s.get("size"):
            out.append({"path": name, "name": name.rsplit("/", 1)[-1], "size": s["size"]})
    out.sort(key=lambda f: f["size"])
    names = {s.get("rfilename") for s in data.get("siblings") or []}
    # A diffusers repo (model_index.json at the top) keeps its model in per-part folders,
    # which ComfyUI's loaders can't use as single files. image_file() needs to know.
    return {"repo": repo, "gated": bool(data.get("gated")), "files": out,
            "diffusers": "model_index.json" in names}


# --- image files: where each one goes in ComfyUI -------------------------------
# A HuggingFace image repo is not laid out like a Civitai model: it may be a diffusers
# folder tree, a sharded file, or a split model whose parts belong in different ComfyUI
# folders. This suggests a destination and refuses the files that cannot work on their
# own. The suggestion is a default the user can change; the refusals are not.
_SHARD = re.compile(r"-\d{5}-of-\d{5}\.safetensors$")
_DIFFUSERS_DIRS = {"unet", "transformer", "vae", "text_encoder", "text_encoder_2",
                   "text_encoder_3", "safety_checker", "image_encoder", "controlnet",
                   "prior", "decoder", "movq", "tokenizer", "scheduler"}
# diffusers/transformers default names, with or without a variant (".fp16"): meaningless
# once basenamed into a ComfyUI folder, and every repo's would collide.
_GENERIC = re.compile(r"^(diffusion_pytorch_model|model|pytorch_model|adapter_model)"
                      r"(\.[\w-]+)?\.safetensors$")
# ComfyUI's own folder names, as Comfy-Org's repackaged repos use them (split_files/vae/…).
_FOLDER_KIND = {"checkpoints": "checkpoint", "loras": "lora", "lora": "lora", "vae": "vae",
                "diffusion_models": "diffusion_model", "unet": "diffusion_model",
                "text_encoders": "text_encoder", "clip": "text_encoder"}
_TE_HINTS = ("t5xxl", "umt5", "clip_l", "clip_g", "text_encoder", "llava", "qwen_2.5_vl",
             "gemma", "byt5")


def image_file(path: str, repo: str = "", diffusers: bool = False) -> dict:
    """{kind, sure, blocked} for one .safetensors in an image repo. `kind` is a key of
    inventory.WRITABLE; `blocked` is the reason it can't be downloaded, or None."""
    name = path.rsplit("/", 1)[-1]
    parts = [p.lower() for p in path.split("/")[:-1]]
    low = name.lower()
    if _SHARD.search(name):
        return {"kind": None, "sure": False,
                "blocked": "one shard of a split file — ComfyUI can't load it on its own"}
    if diffusers and parts and parts[0] in _DIFFUSERS_DIRS:
        return {"kind": None, "sure": False,
                "blocked": "part of a diffusers folder — ComfyUI wants single-file models"}
    for p in reversed(parts):
        if p in _FOLDER_KIND:
            return {"kind": _FOLDER_KIND[p], "sure": True, "blocked": None}
    if _GENERIC.match(name):
        return {"kind": None, "sure": False,
                "blocked": f"generic name ({name}) — it would collide; get it from the model page"}
    if "lora" in low or "lora" in repo.lower():
        return {"kind": "lora", "sure": False, "blocked": None}
    if ("vae" in low and "novae" not in low and "no-vae" not in low) or low == "ae.safetensors":
        return {"kind": "vae", "sure": False, "blocked": None}
    if any(h in low for h in _TE_HINTS):
        return {"kind": "text_encoder", "sure": False, "blocked": None}
    if any(h in low for h in ("unet", "transformer", "diffusion_model")):
        return {"kind": "diffusion_model", "sure": False, "blocked": None}
    return {"kind": "checkpoint", "sure": False, "blocked": None}


# --- fit hints ---------------------------------------------------------------
_VRAM: tuple[float, int | None] = (0.0, None)


def vram_total() -> int | None:
    """Total VRAM in bytes, or None.

    From the host (services/gpu.py: amdgpu sysfs, nvidia-smi, then ComfyUI). Falls back
    to STACK_UI_VRAM_BYTES, and to None (hints suppressed) when nothing is readable. A
    wrong guess here would be worse than no hint at all.
    """
    from services import gpu

    global _VRAM
    if time.monotonic() - _VRAM[0] < 120 and _VRAM[1]:
        return _VRAM[1]
    g = gpu.read()
    total = int(g["total"]) if g.get("available") and g.get("total") else None
    if total is None:
        env = os.environ.get("STACK_UI_VRAM_BYTES", "").strip()
        total = int(env) if env.isdigit() else None
    _VRAM = (time.monotonic(), total)
    return total


def fit(size: int, vram: int | None, free_disk: int | None = None) -> dict:
    """Advisory band for one file. Explicitly a heuristic, and labelled as one in the UI."""
    out: dict = {"band": "unknown", "note": None, "disk_ok": None}
    if free_disk is not None:
        out["disk_ok"] = free_disk > size * 1.05
    if not vram:
        return out
    need = size + KV_OVERHEAD
    if need <= vram - COMPANION_RESERVE:
        out["band"] = "fits"
        out["note"] = "room for an SD 1.5 checkpoint alongside"
    elif need <= vram * 0.95:
        out["band"] = "tight"
        out["note"] = "fits alone; ComfyUI would contend for VRAM"
    else:
        out["band"] = "partial"
        out["note"] = "needs partial offload — lower KOBOLDCPP_GPU_LAYERS"
    return out


# --- reading what a .safetensors actually holds ----------------------------------
# The filename can't separate an all-in-one checkpoint from a bare diffusion model —
# black-forest-labs' and Comfy-Org's "flux1-schnell.safetensors" are the same name and
# size. The file's own header can: a .safetensors starts with an 8-byte length and a
# JSON index of its tensors, and HuggingFace serves byte ranges, so the tensor names are
# one small request away. Cached per (repo, path); never the weights.
_HEADER_MAX = 16 * 1024 * 1024
_INSPECTED: dict[tuple[str, str], dict] = {}
RESOLVE = "https://huggingface.co/{repo}/resolve/main/{path}"


def _range(url: str, start: int, end: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": f"bytes={start}-{end}"})
    token = os.environ.get("HF_TOKEN", "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            if r.status != 206:                     # ignored the range: don't pull 20 GB
                raise HubError("server does not serve byte ranges")
            return r.read(end - start + 1)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise HubError("access denied — a gated repo needs HF_TOKEN set")
        raise HubError(f"huggingface returned {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HubError(f"cannot reach huggingface: {e}")


def kind_from_tensors(keys: list[str], meta: dict | None = None) -> tuple[str | None, str]:
    """(collection, reason) from a safetensors header's tensor names. None = can't tell."""
    arch = str((meta or {}).get("modelspec.architecture") or "").lower()
    # Any tensor named for LoRA: kohya's lora_unet_*, diffusers' lora_A, XLabs' proj_lora1.
    # A base model never uses the word, so this can't misfire on one.
    if "lora" in arch or any("lora" in k.lower() for k in keys):
        return "lora", "LoRA weights"
    has = lambda *ps: any(k.startswith(ps) for k in keys)    # noqa: E731
    diff = has("model.diffusion_model.")
    vae = has("first_stage_model.", "vae.")
    te = has("cond_stage_model.", "conditioner.", "text_encoders.")
    if diff and (vae or te):
        parts = ["diffusion model"] + (["VAE"] if vae else []) + (["text encoder"] if te else [])
        return "checkpoint", "all-in-one checkpoint (" + ", ".join(parts) + ")"
    if diff or has("double_blocks.", "single_blocks.", "input_blocks.", "joint_blocks.",
                   "transformer_blocks.", "diffusion_model.", "blocks.0.", "layers.0.adaLN"):
        return "diffusion_model", "diffusion model only — needs a separate VAE and text encoder"
    if has("decoder.conv_in", "encoder.conv_in", "decoder.up", "encoder.down"):
        return "vae", "VAE"
    if has("text_model.", "encoder.block.", "shared.weight", "model.layers.", "text_projection",
           "transformer.resblocks.", "language_model."):
        return "text_encoder", "text encoder"
    return None, "unrecognised layout"


def inspect(repo: str, path: str) -> dict:
    """{kind, reason} from the file's header, or raises HubError."""
    key = (repo, path)
    if key in _INSPECTED:
        return _INSPECTED[key]
    if not repo or repo.count("/") != 1 or any(c in repo for c in "?&#"):
        raise HubError(f"not a repo id: {repo!r}")
    if not path.endswith(".safetensors") or ".." in path.split("/"):
        raise HubError(f"not a safetensors path: {path!r}")
    url = RESOLVE.format(repo=urllib.parse.quote(repo), path=urllib.parse.quote(path))
    n = int.from_bytes(_range(url, 0, 7), "little")
    if not 2 <= n <= _HEADER_MAX:
        raise HubError("not a readable safetensors header")
    try:
        header = json.loads(_range(url, 8, 8 + n - 1))
    except ValueError:
        raise HubError("not a readable safetensors header")
    meta = header.pop("__metadata__", None)
    kind, reason = kind_from_tensors(list(header), meta if isinstance(meta, dict) else None)
    out = {"kind": kind, "reason": reason, "tensors": len(header)}
    _INSPECTED[key] = out
    return out