"""Live GPU figures, read from the host rather than from a backend.

Until 2026-11-19 the only GPU numbers this app had came from ComfyUI's `/system_stats`, so
with ComfyUI stopped the dashboard showed nothing, even while KoboldCPP held most of the card.
The host can tell us directly, and both sources are already reachable from this container:

- **AMD**: amdgpu's sysfs counters (`/sys/class/drm/cardN/device/mem_info_*`), readable
  without root. On the Halo box the "VRAM" carve-out is small and models live in **GTT**,
  the system RAM the iGPU borrows, so usable memory is VRAM + GTT, the same sum
  `koboldcpp/start.sh` waits on. Both parts are reported too.
- **NVIDIA**: the host's `nvidia-smi` and NVML, bind-mounted by `run-podman.sh`.
- **Fallback**: ComfyUI's `/system_stats`, when neither of the above can be read.

`read()` never raises: no figures is a normal state, reported with `available: False` and a
`source` that says why.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

# Overridable for tests: a fake sysfs tree, a fake nvidia-smi.
SYSFS = Path(os.environ.get("STACK_UI_SYSFS", "/sys"))
NVIDIA_SMI = os.environ.get("STACK_UI_NVIDIA_SMI", "nvidia-smi")
COMFY = f"http://localhost:{os.environ.get('COMFYUI_PORT') or '8188'}"
UA = "stack-ui/0.5"

# sysfs gives a PCI id, not a marketing name. Only the boxes this stack runs on; anything
# else shows its id, which is still a fact rather than a guess.
_AMD_NAMES = {"1586": "AMD Radeon 8060S (Strix Halo)"}
_CARD = re.compile(r"card\d+")


def _int(p: Path) -> int | None:
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None


def _amd() -> dict | None:
    """The first amdgpu card with memory counters, or None."""
    drm = SYSFS / "class" / "drm"
    try:
        cards = sorted(p for p in drm.iterdir() if _CARD.fullmatch(p.name))
    except OSError:
        return None
    for card in cards:
        dev = card / "device"
        try:
            if (dev / "vendor").read_text().strip() != "0x1002":
                continue
        except OSError:
            continue
        vram_t, vram_u = _int(dev / "mem_info_vram_total"), _int(dev / "mem_info_vram_used")
        gtt_t, gtt_u = _int(dev / "mem_info_gtt_total"), _int(dev / "mem_info_gtt_used")
        if vram_t is None or vram_u is None:
            continue
        total = vram_t + (gtt_t or 0)
        used = vram_u + (gtt_u or 0)
        dev_id = ""
        try:
            dev_id = (dev / "device").read_text().strip().removeprefix("0x")
        except OSError:
            pass
        out = {
            "available": bool(total),
            "device": _AMD_NAMES.get(dev_id, f"AMD GPU 1002:{dev_id}" if dev_id else "AMD GPU"),
            "total": total, "used": used, "free": max(total - used, 0),
            "busy": _int(dev / "gpu_busy_percent"),
            "unified": gtt_t is not None,
            "parts": {"vram": {"total": vram_t, "used": vram_u}},
            "source": f"amdgpu sysfs ({card.name})",
        }
        if gtt_t is not None:
            out["parts"]["gtt"] = {"total": gtt_t, "used": gtt_u or 0}
        return out
    return None


def _nvidia() -> dict | None:
    if not shutil.which(NVIDIA_SMI):
        return None
    try:
        p = subprocess.run(
            [NVIDIA_SMI, "--query-gpu=name,memory.total,memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        name, total, used, util = [f.strip() for f in p.stdout.splitlines()[0].split(",")]
        total_b, used_b = int(total) * 1024 * 1024, int(used) * 1024 * 1024
    except ValueError:
        return None
    return {
        "available": bool(total_b), "device": name,
        "total": total_b, "used": used_b, "free": max(total_b - used_b, 0),
        "busy": int(util) if util.isdigit() else None,
        "unified": False, "source": "nvidia-smi",
    }


def _comfy() -> dict | None:
    try:
        req = urllib.request.Request(f"{COMFY}/system_stats", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=2) as r:
            dev = (json.load(r).get("devices") or [{}])[0]
    except Exception:                                          # noqa: BLE001 - best effort
        return None
    total, free = int(dev.get("vram_total") or 0), int(dev.get("vram_free") or 0)
    if not total:
        return None
    return {
        "available": True, "device": (dev.get("name") or "").split(" : ")[0] or "GPU",
        "total": total, "used": max(total - free, 0), "free": free,
        "busy": None, "unified": False, "source": "comfyui /system_stats",
    }


def read() -> dict:
    """{available, device, total, used, free, busy, unified, source[, parts]} in bytes."""
    order = (_nvidia, _amd) if os.environ.get("STACK_GPU") == "nvidia" else (_amd, _nvidia)
    for probe in (*order, _comfy):
        r = probe()
        if r:
            return r
    return {"available": False,
            "source": "no GPU counters readable here (amdgpu sysfs, nvidia-smi) and ComfyUI is down"}
