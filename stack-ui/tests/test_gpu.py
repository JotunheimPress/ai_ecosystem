"""services/gpu.py against a fake sysfs tree and a fake nvidia-smi (no GPU needed).

    .venv/bin/python -m pytest -q tests
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "appcode"))

from services import gpu  # noqa: E402

GIB = 1024 ** 3


def _card(root: Path, name: str, **files: str) -> None:
    dev = root / "class" / "drm" / name / "device"
    dev.mkdir(parents=True)
    for k, v in files.items():
        (dev / k).write_text(v + "\n")


@pytest.fixture
def nowhere(monkeypatch, tmp_path):
    """No sysfs, no nvidia-smi, no ComfyUI — each test adds back what it needs."""
    monkeypatch.setattr(gpu, "SYSFS", tmp_path / "sys")
    monkeypatch.setattr(gpu, "NVIDIA_SMI", str(tmp_path / "no-nvidia-smi"))
    monkeypatch.setattr(gpu, "COMFY", "http://127.0.0.1:9")      # discard port: refused
    monkeypatch.delenv("STACK_GPU", raising=False)
    return tmp_path


def test_amd_unified_sums_vram_and_gtt(nowhere):
    root = nowhere / "sys"
    # A connector entry first: it has no memory counters and must be skipped.
    (root / "class" / "drm" / "card0-DP-1" / "device").mkdir(parents=True)
    _card(root, "card0", vendor="0x1002", device="0x1586",
          mem_info_vram_total=str(GIB // 2), mem_info_vram_used=str(GIB // 4),
          mem_info_gtt_total=str(94 * GIB), mem_info_gtt_used=str(33 * GIB),
          gpu_busy_percent="12")
    g = gpu.read()
    assert g["available"] and g["unified"]
    assert g["total"] == GIB // 2 + 94 * GIB
    assert g["used"] == GIB // 4 + 33 * GIB
    assert g["free"] == g["total"] - g["used"]
    assert g["busy"] == 12
    assert g["device"] == "AMD Radeon 8060S (Strix Halo)"
    assert g["parts"]["gtt"]["used"] == 33 * GIB
    assert g["source"] == "amdgpu sysfs (card0)"


def test_non_amd_card_is_skipped(nowhere):
    _card(nowhere / "sys", "card0", vendor="0x8086", mem_info_vram_total="1", mem_info_vram_used="0")
    g = gpu.read()
    assert not g["available"]
    assert "ComfyUI is down" in g["source"]


def test_nvidia_smi(nowhere, monkeypatch):
    smi = nowhere / "nvidia-smi"
    smi.write_text("#!/bin/sh\necho 'NVIDIA GeForce RTX 4070, 12282, 9000, 37'\n")
    smi.chmod(0o755)
    monkeypatch.setattr(gpu, "NVIDIA_SMI", str(smi))
    monkeypatch.setenv("STACK_GPU", "nvidia")
    g = gpu.read()
    assert g["source"] == "nvidia-smi" and not g["unified"]
    assert g["total"] == 12282 * 1024 * 1024 and g["used"] == 9000 * 1024 * 1024
    assert g["busy"] == 37 and g["device"] == "NVIDIA GeForce RTX 4070"


def test_nvidia_smi_failing_falls_through(nowhere, monkeypatch):
    smi = nowhere / "nvidia-smi"
    smi.write_text("#!/bin/sh\necho 'NVIDIA-SMI has failed' >&2\nexit 9\n")
    smi.chmod(0o755)
    monkeypatch.setattr(gpu, "NVIDIA_SMI", str(smi))
    assert gpu.read()["available"] is False
