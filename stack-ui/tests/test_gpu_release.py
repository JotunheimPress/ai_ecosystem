"""`stack gpu-release` / `gpu-restore` (jp-parent#50) against a fake podman that plays
koboldcpp-local: its state is two files (status: running|exited, model: a name or "inactive"),
and its admin API flips the model the way KoboldCPP's /api/admin/reload_config does. No real
container, GPU or router is touched (STACK_GPU=none, and these commands never use the router).

    .venv/bin/python -m pytest -q tests/test_gpu_release.py
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[2] / "stack"

FAKE_PODMAN = r"""#!/usr/bin/env bash
S="$FAKE_STATE"
echo "$*" >> "$S/log"
case "$1" in
  inspect) [ -f "$S/status" ] && cat "$S/status" || exit 125 ;;
  stop)    echo exited > "$S/status" ;;
  exec)
    [ "$2" = koboldcpp-local ] || exit 1          # comfyui-local: not running here
    [ "$(cat "$S/status")" = running ] || exit 1
    if [ "$3" = curl ]; then                      # released, 1.111.2 drops the prefix
      m=$(cat "$S/model")
      if [ "$m" = inactive ]; then printf '{"result": "inactive"}'
      else printf '{"result": "koboldcpp/%s"}' "$m"; fi
    else                                          # sh -c '<admin request>' <filename>
      [ -f "$S/admin" ] || { echo '{"success": false}'; exit 0; }
      case "${!#}" in
        unload_model)  echo inactive > "$S/model" ;;
        initial_model) cat "$S/initial" > "$S/model" ;;
      esac
      echo '{"success": true}'
    fi ;;
  *) exit 0 ;;
esac
"""


@pytest.fixture()
def kobold(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "status").write_text("running\n")
    (state / "model").write_text("Qwen3.5-9B\n")
    (state / "initial").write_text("Qwen3.5-9B\n")
    (state / "admin").touch()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    podman = bin_ / "podman"
    podman.write_text(FAKE_PODMAN)
    podman.chmod(0o755)
    eco = tmp_path / "eco"
    eco.mkdir()

    def run(*args):
        return subprocess.run(
            [str(STACK), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env={**os.environ, "AI_ECOSYSTEM": str(eco), "STACK_GPU": "none",
                 "FAKE_STATE": str(state), "PATH": f"{bin_}:{os.environ['PATH']}"})

    def model():
        return (state / "model").read_text().strip()

    return dict(run=run, state=state, model=model)


def test_release_unloads_and_restore_loads_the_same_model(kobold):
    r = kobold["run"]("gpu-release")
    assert r.returncode == 0, r.stdout + r.stderr
    assert kobold["model"]() == "inactive" and "released" in r.stdout
    assert (kobold["state"] / "status").read_text().strip() == "running"   # container kept

    r = kobold["run"]("gpu-release", "koboldcpp")
    assert r.returncode == 0 and "already released" in r.stdout

    r = kobold["run"]("gpu-restore")
    assert r.returncode == 0, r.stdout + r.stderr
    assert kobold["model"]() == "Qwen3.5-9B" and "restored Qwen3.5-9B" in r.stdout

    r = kobold["run"]("gpu-restore")
    assert r.returncode == 0 and "already has Qwen3.5-9B loaded" in r.stdout


def test_release_without_admin_mode_stops_the_container(kobold):
    (kobold["state"] / "admin").unlink()           # started before this existed
    r = kobold["run"]("gpu-release")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no admin mode" in r.stdout
    assert (kobold["state"] / "status").read_text().strip() == "exited"
    assert "stop koboldcpp-local" in (kobold["state"] / "log").read_text()


def test_nothing_to_release_when_it_is_not_running(kobold):
    (kobold["state"] / "status").write_text("exited\n")
    r = kobold["run"]("gpu-release")
    assert r.returncode == 0 and "nothing to release" in r.stdout


def test_only_the_text_model_and_usage(kobold):
    r = kobold["run"]("gpu-release", "fortuna")
    assert r.returncode == 1 and "NGL=0" in r.stdout
    r = kobold["run"]("gpu-release", "comfyui")
    assert r.returncode == 1 and "Release ComfyUI VRAM" in r.stdout
    r = kobold["run"]("gpu-restore", "nonsense")
    assert r.returncode == 1 and "usage" in r.stdout
    assert kobold["model"]() == "Qwen3.5-9B"


def test_new_verbs_are_reserved_for_frontend_names():
    text = STACK.read_text()
    reserved = text[text.index("FRONTEND_RESERVED=("):].split(")", 1)[0]
    assert "gpu-release" in reserved and "gpu-restore" in reserved


# --- the dashboard's side --------------------------------------------------------------------
import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "appcode"))

from services import backends, inventory, stackctl  # noqa: E402


def test_release_and_restore_verbs_are_fixed_argvs():
    assert stackctl.resolve("gpu_release_text", None) == ["gpu-release", "koboldcpp"]
    assert stackctl.resolve("gpu_restore_text", None) == ["gpu-restore", "koboldcpp"]
    for v in ("gpu_release_text", "gpu_restore_text"):
        assert stackctl.VERBS[v].mutating and stackctl.VERBS[v].target is None


def test_a_released_model_is_not_a_model_called_inactive(monkeypatch, tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "Local-7B.gguf").write_bytes(b"GGUF")
    monkeypatch.setattr(inventory, "KOBOLD_MODELS", models)
    monkeypatch.setattr(inventory, "MODEL_ENV", tmp_path / "model.env")
    (tmp_path / "model.env").write_text("KOBOLDCPP_MODEL=Local-7B.gguf\n")
    monkeypatch.setattr(inventory, "ROUTE_FILE", tmp_path / "route.conf")
    monkeypatch.setattr(inventory, "running_context", lambda: 8192)
    monkeypatch.setattr(inventory.provenance, "urls", lambda _k: {})

    monkeypatch.setattr(inventory, "running_model", lambda: inventory.RELEASED)
    k = inventory.kobold()
    assert k["released"] and k["running"] is None
    assert not k["restart_needed"]               # nothing loaded to compare with the selection
    assert not any(m["running"] for m in k["models"])
    assert backends.kobold()["released"] and not backends.kobold()["up"]

    monkeypatch.setattr(inventory, "running_model", lambda: "Local-7B")
    k = inventory.kobold()
    assert not k["released"] and k["running"] == "Local-7B"
