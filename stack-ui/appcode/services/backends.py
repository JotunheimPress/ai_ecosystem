"""What each backend has loaded right now — asked of the backends themselves.

The dashboard's top section. Two different kinds of answer, and they are kept apart:

- **KoboldCPP says what it loaded.** `/api/v1/model` names the model in VRAM, so that line
  is a fact. Next to it sits the selection in `model.env`, which only reaches VRAM on a
  restart — the gap between the two is what the "restart to apply" banner is for.
- **ComfyUI does not.** It has no endpoint listing resident models; it loads per job and
  keeps what it can. What it *does* report is how much VRAM torch is holding
  (`/system_stats`) and which files the most recent job named (`/history`, or `/queue`
  while one is running). So the card says "holding 6.4 GB · last job used X" — never
  "X is loaded", which would be a guess presented as a fact.

Every call is best-effort with a short timeout: a backend that is down is a normal state
here, not an error.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from services import inventory, vram

KOBOLD = "http://localhost:5001"
COMFY = vram.COMFY
# Fortuna's own /health, not /v1/models: that needs the API key when one is set, and
# /health already names the model ({"ok": true, "model": "jevk5-4b-q8"}).
FORTUNA = "http://127.0.0.1:8009"

# File types a ComfyUI loader input names. Matching on the value's extension (rather than a
# list of loader node classes) keeps custom-node loaders working without a table to maintain.
_MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft", ".onnx")

# Below this, torch holding memory means "warm CUDA context", not "a model is resident".
_RESIDENT_FLOOR = 256 * 1024 * 1024


def _get(url: str, timeout: float = 2.0):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": vram.UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def kobold() -> dict:
    inv = inventory.kobold()
    running = inv.get("running")
    out = {
        "up": bool(running),
        "loaded": running,
        "selected": inv.get("active"),
        "selected_source": inv.get("source"),
        "selected_present": inv.get("active_present"),
        "gpu_layers": inv.get("gpu_layers"),
        "restart_needed": inv.get("restart_needed"),
        "restart_for": inv.get("restart_for") or [],
        "text_local": inv.get("text_local", True),
        "text_route": inv.get("text_route"),
    }
    if not running:
        return out
    ctx = _get(f"{KOBOLD}/api/extra/true_max_context_length") or {}
    ver = _get(f"{KOBOLD}/api/extra/version") or {}
    out["context"] = ctx.get("value")
    out["version"] = ver.get("version")
    for m in inv.get("models", []):
        if m.get("running"):
            meta = vram.gguf_meta(Path(m["path"]))
            out.update(file=m["name"], size=m["size"], n_layers=meta.get("n_layers"),
                       arch=meta.get("arch"))
            break
    return out


def _models_in(prompt_nodes) -> list[dict]:
    """Model files a workflow names, in node order, deduplicated."""
    found, seen = [], set()
    if not isinstance(prompt_nodes, dict):
        return found
    for node in prompt_nodes.values():
        if not isinstance(node, dict):
            continue
        for key, value in (node.get("inputs") or {}).items():
            if (isinstance(value, str) and value.lower().endswith(_MODEL_EXTS)
                    and value not in seen):
                seen.add(value)
                kind = key[:-5] if key.endswith("_name") else key
                found.append({"kind": kind, "name": value,
                              "node": node.get("class_type") or ""})
    return found


def _finished_at(entry: dict) -> float | None:
    """The last timestamp ComfyUI recorded for a history entry, in epoch seconds."""
    msgs = ((entry.get("status") or {}).get("messages")) or []
    stamps = [m[1].get("timestamp") for m in msgs
              if isinstance(m, list) and len(m) == 2 and isinstance(m[1], dict)
              and isinstance(m[1].get("timestamp"), (int, float))]
    return max(stamps) / 1000 if stamps else None


def comfy() -> dict:
    stats = _get(f"{COMFY}/system_stats")
    if not stats:
        return {"up": False}
    dev = (stats.get("devices") or [{}])[0]
    held = int(dev.get("torch_vram_total") or 0)
    out = {
        "up": True,
        "version": (stats.get("system") or {}).get("comfyui_version"),
        "device": (dev.get("name") or "").split(" : ")[0],
        "vram_total": dev.get("vram_total"),
        "vram_free": dev.get("vram_free"),
        "held": held,
        "resident": held >= _RESIDENT_FLOOR,
        "running": 0, "pending": 0,
        "models": [], "models_from": None, "last_job_at": None,
    }

    queue = _get(f"{COMFY}/queue") or {}
    running = queue.get("queue_running") or []
    out["running"] = len(running)
    out["pending"] = len(queue.get("queue_pending") or [])
    # queue item: [number, prompt_id, prompt, extra, outputs]
    if running and isinstance(running[0], list) and len(running[0]) > 2:
        out["models"] = _models_in(running[0][2])
        out["models_from"] = "running"
        return out

    hist = _get(f"{COMFY}/history?max_items=1") or {}
    if isinstance(hist, dict) and hist:
        entry = list(hist.values())[-1]
        prompt = entry.get("prompt") if isinstance(entry, dict) else None
        if isinstance(prompt, list) and len(prompt) > 2:
            out["models"] = _models_in(prompt[2])
            out["models_from"] = "last"
            out["last_job_at"] = _finished_at(entry)
    return out


def fortuna() -> dict:
    """What Fortuna has loaded, plus what it boots next and whether that file is verified —
    the card offers the restart when the two differ, as the text card does."""
    h = _get(f"{FORTUNA}/health")
    up = isinstance(h, dict) and bool(h.get("ok"))
    model = h.get("model") if up and isinstance(h.get("model"), str) else None
    inv = inventory.fortuna_models(running=model)
    out = {"up": up, "selected": inv["selected"], "selected_integrity": inv["selected_integrity"],
           "restart_needed": inv["restart_needed"]}
    if up:
        out["model"] = model
    return out


# One adapter per capability (`stack status --json`), so a card is chosen by what a backend
# provides rather than by its name.
ADAPTERS = {"text": "kobold", "image-sound": "comfy", "decision": "fortuna"}


def snapshot() -> dict:
    return {"kobold": kobold(), "comfy": comfy(), "fortuna": fortuna(), "at": time.time()}
