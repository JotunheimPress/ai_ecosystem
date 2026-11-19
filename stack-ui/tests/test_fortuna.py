"""Fortuna's model selection and verification: models.table, the integrity stamps start.sh
writes, fortuna.env writes, delete refusals, the allowlisted verbs, and the VRAM planner's share.
The shell half (start.sh --verify / --check) runs against a tiny fake table.

    .venv/bin/python -m pytest -q tests/test_fortuna.py
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "appcode"))

from services import inventory, stackctl, vram  # noqa: E402

RECIPE = Path(__file__).resolve().parents[2] / "fortuna"
REAL_TABLE = RECIPE / "models.table"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


GOOD, OTHER = b"GGUF good file", b"GGUF other file"


def _clean_env(**extra) -> dict:
    """The caller's environment minus any FORTUNA_* that would point start.sh elsewhere."""
    return {**{k: v for k, v in os.environ.items() if not k.startswith("FORTUNA_")}, **extra}


@pytest.fixture()
def fort(monkeypatch, tmp_path):
    """A fake table of two models, a state dir, GPU=amd, Fortuna down."""
    table = tmp_path / "models.table"
    table.write_text(
        "# test\nREPO=someone/Fake-GGUF\nREV=0123456789abcdef\n"
        f"fake-a|a.gguf|{_sha(GOOD)}|{len(GOOD)}|1.22|0.93|amd,none\n"
        f"fake-b|b.gguf|{_sha(OTHER)}|{len(OTHER)}|1.3|1.0|nvidia\n")
    state = tmp_path / "state"
    (state / "models").mkdir(parents=True)
    monkeypatch.setattr(inventory, "FORTUNA_TABLE", table)
    monkeypatch.setattr(inventory, "FORTUNA_STATE", state)
    monkeypatch.setattr(inventory, "FORTUNA_MODELS", state / "models")
    monkeypatch.setattr(inventory, "FORTUNA_ENV", state / "fortuna.env")
    monkeypatch.setitem(inventory.WRITABLE, "fortuna", (state / "models", (".gguf",)))
    monkeypatch.setattr(inventory, "running_fortuna", lambda: None)
    monkeypatch.setenv("STACK_GPU", "amd")
    return state


def _stamp(f: Path, sha: str, ext: str = ".verified") -> None:
    st = f.stat()
    f.with_name(f.name + ext).write_text(f"{st.st_size} {int(st.st_mtime)} {sha}\n")


def _row(name: str) -> dict:
    return next(m for m in inventory.fortuna_models()["models"] if m["name"] == name)


# --- the table -----------------------------------------------------------------------
def test_real_table_parses_and_has_one_default_per_gpu():
    assert inventory.FORTUNA_TABLE.resolve() == REAL_TABLE.resolve()
    t = inventory.fortuna_table()
    assert t["repo"] and len(t["rev"]) == 40
    assert len(t["models"]) >= 1
    for m in t["models"]:
        assert len(m["sha256"]) == 64 and m["bytes"] > 0 and m["file"].endswith(".gguf")
    for gpu in ("nvidia", "amd", "none"):
        assert sum(gpu in m["default_for"] for m in t["models"]) == 1, gpu


def test_start_sh_lists_the_same_names():
    out = subprocess.run([str(RECIPE / "start.sh"), "--models"], capture_output=True, text=True,
                         env=_clean_env(STACK_GPU="none"), check=True).stdout
    listed = [line.split()[0] for line in out.splitlines() if line.strip()]
    assert listed == [m["name"] for m in inventory.fortuna_table()["models"]]


# --- integrity --------------------------------------------------------------------------
def test_integrity_states(fort):
    models = fort / "models"
    assert _row("fake-a")["integrity"] == "absent"
    (models / "a.gguf.part").write_bytes(GOOD[:4])
    assert _row("fake-a")["integrity"] == "partial" and _row("fake-a")["part_bytes"] == 4
    (models / "a.gguf.part").rename(models / "a.gguf")
    (models / "a.gguf").write_bytes(GOOD)
    assert _row("fake-a")["integrity"] == "unchecked"
    _stamp(models / "a.gguf", _sha(GOOD))
    assert _row("fake-a")["integrity"] == "verified"
    # Changed after it was hashed: the stamp no longer vouches for it.
    (models / "a.gguf").write_bytes(GOOD + b"!")
    assert _row("fake-a")["integrity"] == "unchecked"
    (models / "b.gguf").write_bytes(b"tampered")
    _stamp(models / "b.gguf", _sha(OTHER), ".mismatch")
    assert _row("fake-b")["integrity"] == "mismatch"


def test_default_follows_the_gpu(fort, monkeypatch):
    f = inventory.fortuna_models()
    assert f["selected"] == "fake-a" and f["source"] == "default for amd"
    monkeypatch.setenv("STACK_GPU", "nvidia")
    assert inventory.fortuna_models()["selected"] == "fake-b"


# --- selection ----------------------------------------------------------------------------
def test_select_writes_model_and_keeps_other_lines(fort):
    (fort / "models" / "b.gguf").write_bytes(OTHER)
    (fort / "fortuna.env").write_text("# mine\nAPI_KEY=secret\nMODEL=fake-a\n")
    f = inventory.select_fortuna("fake-b")
    assert f["selected"] == "fake-b" and f["source"] == "fortuna.env"
    assert (fort / "fortuna.env").read_text() == "# mine\nAPI_KEY=secret\nMODEL=fake-b\n"


def test_select_refuses_unknown_absent_and_bad(fort):
    with pytest.raises(inventory.InventoryError, match="unknown"):
        inventory.select_fortuna("../etc")
    with pytest.raises(inventory.InventoryError, match="not downloaded"):
        inventory.select_fortuna("fake-b")
    (fort / "models" / "b.gguf").write_bytes(b"tampered")
    _stamp(fort / "models" / "b.gguf", _sha(OTHER), ".mismatch")
    with pytest.raises(inventory.InventoryError, match="sha256"):
        inventory.select_fortuna("fake-b")
    assert not (fort / "fortuna.env").exists()


def test_restart_needed_only_when_loaded_differs(fort, monkeypatch):
    monkeypatch.setattr(inventory, "running_fortuna", lambda: "fake-b")
    assert inventory.fortuna_models()["restart_needed"] is True
    assert inventory.fortuna_models(running="fake-a")["restart_needed"] is False
    assert inventory.fortuna_models(running=None)["restart_needed"] is False


# --- delete -----------------------------------------------------------------------------------
def test_delete_refuses_selected_and_loaded_and_clears_stamps(fort, monkeypatch):
    models = fort / "models"
    (models / "a.gguf").write_bytes(GOOD)
    (models / "b.gguf").write_bytes(OTHER)
    _stamp(models / "b.gguf", _sha(OTHER))
    with pytest.raises(inventory.InventoryError, match="boots"):
        inventory.delete_model("fortuna", "a.gguf")
    monkeypatch.setattr(inventory, "running_fortuna", lambda: "fake-b")
    with pytest.raises(inventory.InventoryError, match="loaded"):
        inventory.delete_model("fortuna", "b.gguf")
    monkeypatch.setattr(inventory, "running_fortuna", lambda: None)
    monkeypatch.setattr(inventory.provenance, "forget", lambda *_: None)
    assert inventory.delete_model("fortuna", "b.gguf")["freed"] == len(OTHER)
    assert not (models / "b.gguf.verified").exists() and not (models / "b.gguf").exists()


# --- the allowlisted verbs ------------------------------------------------------------------
def test_fortuna_verbs_take_only_table_names(fort):
    assert stackctl.resolve("fortuna_fetch", "fake-a") == ["fortuna-model", "fetch", "fake-a"]
    assert stackctl.resolve("fortuna_verify", "fake-b") == ["fortuna-model", "verify", "fake-b"]
    for bad in ("", "a.gguf", "fake-a; rm -rf /", "https://evil/x.gguf"):
        with pytest.raises(stackctl.StackError):
            stackctl.resolve("fortuna_fetch", bad)


# --- VRAM planner ------------------------------------------------------------------------------
def test_plan_reserves_fortunas_share(tmp_path):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")                         # unreadable header -> patched below
    vram._GGUF_CACHE[f"{model}:{model.stat().st_mtime_ns}"] = {"n_layers": 10}
    gb = 1024 ** 3
    alone = vram.plan(model, 5 * gb, 0, 8 * gb)
    with_f = vram.plan(model, 5 * gb, 0, 8 * gb, fortuna_size=3 * gb)
    assert with_f["fortuna_size"] == 3 * gb
    assert with_f["suggested_layers"] < alone["suggested_layers"]


# --- start.sh: --verify and --check on a fake table ------------------------------------------
def _start(tmp_path, *args, model="fake-a"):
    return subprocess.run(
        [str(RECIPE / "start.sh"), *args], capture_output=True, text=True,
        env=_clean_env(STACK_GPU="none", FORTUNA_TABLE=str(tmp_path / "models.table"),
                       FORTUNA_STATE=str(tmp_path / "state"), FORTUNA_MODEL=model))


def test_start_sh_verify_and_check(fort, tmp_path):
    models = fort / "models"
    r = _start(tmp_path, "--check")
    assert r.returncode == 1 and "not downloaded" in r.stdout
    (models / "a.gguf").write_bytes(GOOD)
    r = _start(tmp_path, "--check")
    assert r.returncode == 1 and "not verified yet" in r.stdout
    assert _start(tmp_path, "--verify", "fake-a").returncode == 0
    assert _row("fake-a")["integrity"] == "verified"            # Python reads the bash stamp
    r = _start(tmp_path, "--check")
    assert r.returncode == 0 and "sha256 verified" in r.stdout
    (models / "b.gguf").write_bytes(b"tampered")
    r = _start(tmp_path, "--verify", "fake-b")
    assert r.returncode == 1 and "MISMATCH" in r.stderr
    assert _row("fake-b")["integrity"] == "mismatch"
    r = _start(tmp_path, "--check", model="fake-b")
    assert r.returncode == 1 and "FAILED" in r.stdout
    # Starting with a recorded mismatch is refused before anything touches podman.
    r = _start(tmp_path, model="fake-b")
    assert r.returncode == 1 and "not starting" in r.stderr
