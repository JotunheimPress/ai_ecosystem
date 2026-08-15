"""What models are actually on disk, and which one Kobold will boot.

85 GB of weights have accumulated across the two backends with no inventory anywhere —
this is the read-only half of fixing that. Nothing here writes.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from .stackctl import ECO

KOBOLD_MODELS = ECO / "koboldcpp" / "models"
MODEL_ENV = ECO / "koboldcpp" / "model.env"
START_SH = ECO / "ai-stack" / "koboldcpp" / "start.sh"
COMFY_MODELS = Path(os.environ.get("COMFYUI_STATE") or (ECO / "ComfyUI")) / "models"

# Subtrees worth showing separately. Anything else under models/ is rolled up as "other".
COMFY_KINDS = ["checkpoints", "loras", "vae", "photomaker", "insightface", "unet", "clip"]

_CACHE: dict[str, tuple[float, object]] = {}
_TTL = 30.0  # the ComfyUI tree is ~70 GB; walk it at most twice a minute


def _cached(key: str, fn):
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def _tree_size(p: Path) -> tuple[int, int]:
    """(bytes, file count) — metadata only, never opens a file."""
    total = count = 0
    if not p.is_dir():
        return 0, 0
    for root, _dirs, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            try:
                total += os.stat(os.path.join(root, f)).st_size
                count += 1
            except OSError:
                pass
    return total, count


# The built-in default lives in start.sh, so read it from there rather than keeping a
# second copy of the filename here that could drift. This regex is a deliberate, narrow
# coupling to that one line; if it ever stops matching we report "unknown" rather than
# guessing, which is why the fallback is None and not a hardcoded name.
_DEFAULT = re.compile(r"KOBOLDCPP_MODEL:-\$\{FILE_MODEL:-([^}]+)\}")


def _builtin_default() -> str | None:
    try:
        m = _DEFAULT.search(START_SH.read_text())
        return m.group(1) if m else None
    except OSError:
        return None


def _read_model_env() -> dict[str, str]:
    """Same strict two-key parse as start.sh — never exec, never eval."""
    out: dict[str, str] = {}
    try:
        for line in MODEL_ENV.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() in ("KOBOLDCPP_MODEL", "KOBOLDCPP_GPU_LAYERS"):
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def kobold() -> dict:
    env = _read_model_env()
    selected = env.get("KOBOLDCPP_MODEL")
    active = selected or _builtin_default()
    files = []
    if KOBOLD_MODELS.is_dir():
        for f in sorted(KOBOLD_MODELS.glob("*.gguf")):
            try:
                size = f.stat().st_size
            except OSError:
                continue
            files.append({"name": f.name, "size": size, "active": f.name == active})
    return {
        "dir": str(KOBOLD_MODELS),
        "models": files,
        "active": active,
        # Where the selection comes from matters: "default" means it is not pinned and a
        # future `stack up` on another machine would boot something else.
        "source": "model.env" if selected else ("start.sh default" if active else "unknown"),
        "gpu_layers": env.get("KOBOLDCPP_GPU_LAYERS"),
        # An active model that isn't on disk is a start.sh hard failure, so surface it here.
        "active_present": any(f["active"] for f in files) if active else False,
    }


def comfyui() -> dict:
    def build():
        kinds, seen = [], set()
        for kind in COMFY_KINDS:
            p = COMFY_MODELS / kind
            if not p.is_dir():
                continue
            size, count = _tree_size(p)
            seen.add(kind)
            kinds.append({"kind": kind, "size": size, "count": count})
        other_size = other_count = 0
        if COMFY_MODELS.is_dir():
            for p in COMFY_MODELS.iterdir():
                if p.is_dir() and p.name not in seen:
                    s, c = _tree_size(p)
                    other_size += s
                    other_count += c
        if other_count:
            kinds.append({"kind": "other", "size": other_size, "count": other_count})
        return {"dir": str(COMFY_MODELS), "kinds": kinds,
                "total": sum(k["size"] for k in kinds)}
    return _cached("comfyui", build)


def checkpoints() -> list[dict]:
    """Individual checkpoint files — the ones you pick between in the other frontends."""
    def build():
        p = COMFY_MODELS / "checkpoints"
        out = []
        if p.is_dir():
            for f in sorted(p.iterdir()):
                if f.suffix in (".safetensors", ".ckpt"):
                    try:
                        out.append({"name": f.name, "size": f.stat().st_size})
                    except OSError:
                        pass
        return out
    return _cached("checkpoints", build)
