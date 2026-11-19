"""stackctl.status() (JSON, with the text fallback), the route verbs, the text-route-aware
restart banner, and the Fortuna adapter. No stack, no backends: everything is faked.

    .venv/bin/python -m pytest -q tests
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "appcode"))

from services import backends, inventory, stackctl  # noqa: E402

STATUS = {
    "version": 1, "boot": "on", "switch": "on",
    "route": {"text": "halo.lan:5001", "where": "halo", "router_config": "current"},
    "apps": [
        {"name": "router", "group": "backend", "label": "Router", "capability": "routing",
         "port": 5001, "up": True, "container": "backend-router", "description": "Routes text"},
        {"name": "koboldcpp", "group": "backend", "label": "Oracle", "capability": "text",
         "port": 5001, "up": True, "container": "koboldcpp-local", "description": "LLM",
         "local": {"state": "stopped", "probed": True}},
        {"name": "companion-assistant", "group": "frontend", "label": "Cantinero",
         "capability": None, "port": 7867, "up": False, "container": "companion-assistant",
         "description": "bar", "icon": "mdi-account-voice", "https_port": 7868,
         "https_setup": "/ca"},
        {"name": "stackui", "group": "control", "label": "AI Ecosystem", "capability": None,
         "port": 7863, "up": True, "container": "stackui", "description": "this"},
        "not an app",
    ],
    "warnings": ["x/frontend.json skipped: bad port"],
}

TEXT = """Start at boot: ON  (stack boot off to change)
Backends (network: ai-net)
  router           [up  ]  :5001  Routes text → local
Frontends
  companion        [down]  :7861  Character chat
Control
  stackui          [up  ]  :7863  Control dashboard
"""


def _fake_run(json_out: str | None, json_rc: int = 0):
    def run(verb, target=None):
        if verb == "status_json":
            if json_out is None:
                raise stackctl.StackError("no stack")
            return {"exit_code": json_rc, "stdout": json_out, "output": json_out, "duration_ms": 7}
        if verb == "status":
            return {"exit_code": 0, "stdout": TEXT, "output": TEXT, "duration_ms": 9}
        if verb == "frontends":
            return {"exit_code": 0, "stdout": "{}", "output": "{}", "duration_ms": 1}
        raise AssertionError(verb)
    return run


def test_status_from_json(monkeypatch):
    monkeypatch.setattr(stackctl, "run", _fake_run(json.dumps(STATUS)))
    st = stackctl.status()
    assert st["source"] == "json" and st["boot"] == "ON" and st["parsed"]
    assert st["route"]["where"] == "halo"
    assert st["warnings"] == ["x/frontend.json skipped: bad port"]
    by = {a["name"]: a for a in st["apps"]}
    assert set(by) == {"router", "koboldcpp", "companion-assistant", "stackui"}
    assert by["koboldcpp"]["group"] == "backends" and by["koboldcpp"]["capability"] == "text"
    assert by["koboldcpp"]["local"] == {"state": "stopped", "probed": True}
    assert by["companion-assistant"]["https_port"] == 7868
    assert by["companion-assistant"]["https_setup"] == "/ca"
    assert by["stackui"]["self"] and by["stackui"]["group"] == "control"


@pytest.mark.parametrize("out,rc", [(None, 0), ("usage: stack status", 0), ("{}", 0),
                                    (json.dumps(STATUS), 1)])
def test_status_falls_back_to_text(monkeypatch, out, rc):
    monkeypatch.setattr(stackctl, "run", _fake_run(out, rc))
    monkeypatch.setattr(stackctl, "labels", lambda: {})
    st = stackctl.status()
    assert st["source"] == "text" and st["boot"] == "ON"
    assert [a["name"] for a in st["apps"]] == ["router", "companion", "stackui"]
    assert st["route"] is None and st["warnings"] == []


def test_known_apps_from_json(monkeypatch):
    monkeypatch.setattr(stackctl, "run", _fake_run(json.dumps(STATUS)))
    stackctl.invalidate_apps()
    try:
        assert "koboldcpp" in stackctl.known_apps(force=True)
    finally:
        stackctl.invalidate_apps()


def test_route_verbs_take_no_target():
    assert stackctl.resolve("route_text_local", None) == ["route", "local"]
    assert stackctl.resolve("route_text_halo", None) == ["route", "halo"]
    with pytest.raises(stackctl.StackError):
        stackctl.resolve("route_text_halo", "evil.example")


@pytest.fixture
def kobold_dir(monkeypatch, tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "Local-7B.gguf").write_bytes(b"GGUF")
    monkeypatch.setattr(inventory, "KOBOLD_MODELS", models)
    monkeypatch.setattr(inventory, "MODEL_ENV", tmp_path / "model.env")
    (tmp_path / "model.env").write_text("KOBOLDCPP_MODEL=Local-7B.gguf\n")
    monkeypatch.setattr(inventory, "ROUTE_FILE", tmp_path / "route.conf")
    monkeypatch.setattr(inventory, "running_model", lambda: "Halo-70B")
    monkeypatch.setattr(inventory, "running_context", lambda: 32768)
    monkeypatch.setattr(inventory.provenance, "urls", lambda _k: {})
    return tmp_path


def test_restart_banner_when_local(kobold_dir):
    (kobold_dir / "route.conf").write_text("set $kobold_up koboldcpp-local:5001;\n")
    k = inventory.kobold()
    assert k["text_local"] and k["restart_needed"] and "model" in k["restart_for"]


def test_no_restart_banner_when_routed_to_halo(kobold_dir):
    (kobold_dir / "route.conf").write_text("set $kobold_up halo.lan:5001;\n")
    k = inventory.kobold()
    assert not k["text_local"] and k["text_route"] == "halo.lan:5001"
    assert not k["restart_needed"] and k["restart_for"] == []
    assert not any(m["running"] for m in k["models"])


def test_missing_route_file_means_local(kobold_dir):
    assert inventory.text_is_local()


def test_fortuna_adapter(monkeypatch):
    def inv(running=None):
        return {"selected": "jevk5-4b-q8", "selected_integrity": "verified",
                "restart_needed": bool(running) and running != "jevk5-4b-q8"}
    monkeypatch.setattr(backends.inventory, "fortuna_models", inv)
    monkeypatch.setattr(backends, "_get", lambda url, timeout=2.0: {"ok": True, "model": "jevk5-4b-q8"})
    assert backends.fortuna() == {"up": True, "model": "jevk5-4b-q8", "selected": "jevk5-4b-q8",
                                  "selected_integrity": "verified", "restart_needed": False}
    monkeypatch.setattr(backends, "_get", lambda url, timeout=2.0: {"ok": True, "model": "jevk5-4b-q4"})
    assert backends.fortuna()["restart_needed"] is True
    monkeypatch.setattr(backends, "_get", lambda url, timeout=2.0: None)
    assert backends.fortuna() == {"up": False, "selected": "jevk5-4b-q8",
                                  "selected_integrity": "verified", "restart_needed": False}
    assert backends.ADAPTERS["decision"] == "fortuna"


def test_older_stack_names_it_switch(monkeypatch):
    old = {k: v for k, v in STATUS.items() if k != "boot"}
    monkeypatch.setattr(stackctl, "run", _fake_run(json.dumps(old)))
    assert stackctl.status()["boot"] == "ON"
    assert stackctl.parse_status("AI backend switch: OFF (gaming mode)\n")["boot"] == "OFF"


def test_boot_verbs_take_no_target_and_on_off_are_gone():
    assert stackctl.resolve("boot_on", None) == ["boot", "on"]
    assert stackctl.resolve("boot_off", None) == ["boot", "off"]
    with pytest.raises(stackctl.StackError):
        stackctl.resolve("boot_on", "koboldcpp")
    for gone in ("on", "off"):
        with pytest.raises(stackctl.StackError):
            stackctl.resolve(gone, None)
