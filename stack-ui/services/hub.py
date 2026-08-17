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


def search(query: str, kind: str = "gguf", limit: int = 25) -> list[dict]:
    """Search models. `filter=gguf` is what makes this usable for Kobold — it returns the
    quant repos directly rather than the base weights."""
    q = {
        "search": query, "sort": "downloads", "direction": "-1",
        "limit": max(1, min(limit, 50)),
    }
    if kind:
        q["filter"] = kind
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
    return {"repo": repo, "gated": bool(data.get("gated")), "files": out}


# --- fit hints ---------------------------------------------------------------
_VRAM: tuple[float, int | None] = (0.0, None)


def vram_total() -> int | None:
    """Total VRAM in bytes, or None.

    There is no NVIDIA tooling in this container by design, so ask ComfyUI — its
    /system_stats reports the device. Falls back to STACK_UI_VRAM_BYTES so the hint still
    works when ComfyUI is stopped, and to None (hints suppressed) when neither is
    available. A wrong guess here would be worse than no hint at all.
    """
    global _VRAM
    if time.monotonic() - _VRAM[0] < 120 and _VRAM[1]:
        return _VRAM[1]
    total = None
    try:
        req = urllib.request.Request("http://localhost:8188/system_stats",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=2) as r:
            devs = json.load(r).get("devices") or []
            if devs and devs[0].get("vram_total"):
                total = int(devs[0]["vram_total"])
    except Exception:                                    # noqa: BLE001 - best effort
        total = None
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
