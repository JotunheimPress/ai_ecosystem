"""VRAM planning across the two backends that share one 12 GB card.

The recurring failure on this machine is documented in `comfyui/CLAUDE.md`: ComfyUI holds
VRAM after a job and doesn't give it back, so KoboldCPP OOMs the instant it starts, its
`--rm` container disappears before logging, and `stack up` reports "did not come up" with
no logs to read. It reads as KoboldCPP being broken when it is ComfyUI still holding 6.4 GB.

So this module does three things: report what's actually resident, offer the `/free` call
that fixes the above, and estimate whether a chosen pairing will fit — including how many
layers to offload so it does.

**Every number here is an estimate and is labelled as one.** The layer count is read from
the GGUF header, which is exact; the VRAM those layers occupy is proportional to file size,
which is not. Live figures come from the host (`services/gpu.py`: amdgpu sysfs or
nvidia-smi), with ComfyUI's `/system_stats` as the fallback.
"""
from __future__ import annotations

import json
import os
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path

# Host port from ai-stack/host.env (COMFYUI_PORT), handed in by run-podman.sh.
COMFY = f"http://localhost:{os.environ.get('COMFYUI_PORT') or '8188'}"
UA = "stack-ui/0.5"

# Rough KV-cache + activation overhead at the context sizes this box runs (8k). Scales
# with layer count, so it's applied per offloaded layer rather than as a flat lump.
KV_PER_LAYER = 40 * 1024 * 1024

# The desktop is on this card too (KDE/Wayland compositor, browser acceleration). Planning
# against the full 12 GB produces answers like "12.3 of 12.5 GB", which is arithmetically
# true and practically an OOM. Held back from every budget.
DESKTOP_HEADROOM = 768 * 1024 * 1024

_GGUF_CACHE: dict[str, dict] = {}


# --- GGUF header -------------------------------------------------------------
# Layout: magic, version, tensor_count, kv_count, then kv pairs. We only want
# <arch>.block_count, which is what KOBOLDCPP_GPU_LAYERS is counting against — guessing 33
# for every model is exactly the kind of confident wrongness this is meant to remove.
_STR, _ARR = 8, 9
_FIXED = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


def _read_str(f) -> str:
    n, = struct.unpack("<Q", f.read(8))
    return f.read(n).decode("utf-8", "replace")


def _skip_value(f, vtype: int) -> None:
    if vtype == _STR:
        _read_str(f)
    elif vtype == _ARR:
        etype, = struct.unpack("<I", f.read(4))
        count, = struct.unpack("<Q", f.read(8))
        if etype == _STR:
            for _ in range(count):
                _read_str(f)
        else:
            f.seek(_FIXED.get(etype, 1) * count, 1)
    else:
        f.seek(_FIXED.get(vtype, 1), 1)


# Attention shape, for the KV-cache estimate (kv_cache_bytes). Scalars only: a model with
# per-layer arrays here just gets no estimate rather than a wrong one.
_ATTN = {".attention.head_count": "n_heads", ".attention.head_count_kv": "n_kv_heads",
         ".attention.key_length": "key_len", ".attention.value_length": "value_len",
         ".embedding_length": "n_embd", ".context_length": "train_ctx",
         # Hybrid models (Qwen3.5): only every Nth layer is attention and keeps a cache.
         ".full_attention_interval": "attn_interval"}
_INT_TYPES = (4, 5, 10, 11)


def gguf_meta(path: Path) -> dict:
    """{arch, n_layers, n_heads, n_kv_heads, key_len, value_len, n_embd, train_ctx} — the
    ones present — from the header, or {} if it isn't readable. Cached by path+mtime."""
    try:
        key = f"{path}:{path.stat().st_mtime_ns}"
    except OSError:
        return {}
    if key in _GGUF_CACHE:
        return _GGUF_CACHE[key]

    meta: dict = {}
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return {}
            struct.unpack("<I", f.read(4))                    # version
            struct.unpack("<Q", f.read(8))                    # tensor count
            n_kv, = struct.unpack("<Q", f.read(8))
            arch = None
            for _ in range(min(n_kv, 512)):                   # headers are small; cap anyway
                key_name = _read_str(f)
                # The tokenizer's vocab arrays follow the model keys and are megabytes;
                # everything wanted here comes before them.
                if key_name.startswith("tokenizer.") and "n_layers" in meta:
                    break
                vtype, = struct.unpack("<I", f.read(4))
                if key_name == "general.architecture" and vtype == _STR:
                    arch = _read_str(f)
                    meta["arch"] = arch
                elif key_name.endswith(".block_count") and vtype in _INT_TYPES:
                    meta["n_layers"] = int.from_bytes(f.read(_FIXED[vtype]), "little")
                elif vtype in _INT_TYPES and arch and key_name.startswith(arch + ".") \
                        and key_name[len(arch):] in _ATTN:
                    meta[_ATTN[key_name[len(arch):]]] = int.from_bytes(f.read(_FIXED[vtype]), "little")
                else:
                    _skip_value(f, vtype)
    except (OSError, struct.error, ValueError):
        return {}
    _GGUF_CACHE[key] = meta
    return meta


def kv_cache_bytes(meta: dict, context: int) -> int | None:
    """KV cache for `context` tokens at f16 (KoboldCPP's default), or None if the header
    lacks the attention shape. Hybrid models that declare full_attention_interval count
    only their attention layers; other hybrids get an upper bound."""
    n_layers, n_kv = meta.get("n_layers"), meta.get("n_kv_heads") or meta.get("n_heads")
    if n_layers and (meta.get("attn_interval") or 0) > 1:
        n_layers = max(1, n_layers // meta["attn_interval"])
    head = meta.get("key_len") or (meta["n_embd"] // meta["n_heads"]
                                   if meta.get("n_embd") and meta.get("n_heads") else None)
    if not (n_layers and n_kv and head):
        return None
    k_len, v_len = head, meta.get("value_len") or head
    return n_layers * context * n_kv * (k_len + v_len) * 2


# --- live figures ------------------------------------------------------------
def stats() -> dict:
    """GPU memory now, from the host when it can be read (services/gpu.py)."""
    from services import gpu

    return gpu.read()


def comfy_up() -> bool:
    """Whether ComfyUI answers, so the UI can offer "Release ComfyUI VRAM" only then."""
    try:
        req = urllib.request.Request(f"{COMFY}/system_stats", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=1) as r:
            return r.status == 200
    except Exception:                                          # noqa: BLE001 - best effort
        return False


def free_vram() -> dict:
    """Ask ComfyUI to unload models and release VRAM.

    This is the documented fix for "KoboldCPP won't start": no restart needed, and it is
    much less disruptive than bouncing the container mid-session.
    """
    body = json.dumps({"unload_models": True, "free_memory": True}).encode()
    req = urllib.request.Request(f"{COMFY}/free", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"could not reach ComfyUI: {e}")
    time.sleep(1.0)                                            # let the free actually land
    return stats()


# --- planning ----------------------------------------------------------------
# Fortuna runs llama-server at a 16,384-token context (fortuna/entrypoint.sh FORTUNA_CTX), all the
# time, with NGL layers on the GPU (fortuna.env; all by default). Its share is the file plus that
# context's KV cache, scaled by the share of layers offloaded (llama.cpp keeps each layer's cache
# where the layer is); for its hybrid Qwen3.5 the cache is small. Compute buffers are not counted,
# like everywhere in this module.
FORTUNA_CTX = 16384


def fortuna_bytes(path: Path, ngl: int = 99) -> int:
    """Estimated GPU memory Fortuna holds with this model loaded on `ngl` GPU layers:
    weights + KV cache, in proportion. 0 layers (CPU only) holds none."""
    if ngl <= 0:
        return 0
    meta = gguf_meta(path)
    full = path.stat().st_size + (kv_cache_bytes(meta, FORTUNA_CTX) or 0)
    n_layers = meta.get("n_layers") or 0
    if not n_layers or ngl >= n_layers:
        return full
    return int(full * ngl / n_layers)


def plan(model_path: Path, model_size: int, checkpoint_size: int, vram_total: int,
         fortuna_size: int = 0) -> dict:
    """How many layers of this model fit alongside that checkpoint.

    Approximation, deliberately simple and stated as such: VRAM for N offloaded layers is
    N/total of the file plus a per-layer KV allowance. Real usage depends on quant format,
    context length and batch size — this is meant to get you to a working number fast, not
    to be authoritative.
    """
    meta = gguf_meta(model_path)
    n_layers = int(meta.get("n_layers") or 0)
    out: dict = {
        "arch": meta.get("arch"),
        "n_layers": n_layers or None,
        "model_size": model_size,
        "checkpoint_size": checkpoint_size,
        "fortuna_size": fortuna_size,
        "vram_total": vram_total,
        "approximate": True,
    }
    if not n_layers or not vram_total:
        out["note"] = ("layer count unreadable from the GGUF header"
                       if not n_layers else "VRAM total unknown — start ComfyUI")
        return out

    budget = vram_total - checkpoint_size - fortuna_size - DESKTOP_HEADROOM
    per_layer = model_size / n_layers + KV_PER_LAYER
    fit = int(budget // per_layer) if per_layer > 0 else 0
    suggested = max(0, min(n_layers, fit))

    out.update({
        "budget": budget,
        "headroom": DESKTOP_HEADROOM,
        "per_layer": int(per_layer),
        "suggested_layers": suggested,
        "full_offload_needs": int(per_layer * n_layers),
        "fits_fully": suggested >= n_layers,
        "estimated_vram": int(per_layer * suggested),
    })
    if suggested <= 0:
        out["note"] = ("that checkpoint and Fortuna leave no room — pick smaller ones, or run them apart"
                       if fortuna_size else
                       "that checkpoint leaves no room — pick a smaller one, or run them apart")
    elif suggested >= n_layers:
        out["note"] = f"all {n_layers} layers fit alongside it"
    else:
        out["note"] = f"offload {suggested} of {n_layers} layers; the rest runs on CPU"
    return out
