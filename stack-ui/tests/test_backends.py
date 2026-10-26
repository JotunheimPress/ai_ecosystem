"""services/backends.py against fake KoboldCPP and ComfyUI servers (no GPU, no network).

    .venv/bin/python -m pytest -q tests
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "appcode"))

from services import backends, inventory  # noqa: E402

PROMPT = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "juggernautXL.safetensors"}},
    "5": {"class_type": "LoraLoader", "inputs": {"lora_name": "detail.safetensors", "model": ["4", 0],
                                                 "strength_model": 1.0}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat.safetensors-free prompt"}},
    "7": {"class_type": "VAELoader", "inputs": {"vae_name": "sdxl_vae.safetensors"}},
    "8": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "juggernautXL.safetensors"}},
}


def _server(routes: dict):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = routes.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _stats(torch_total: int) -> dict:
    return {"system": {"comfyui_version": "0.31.1"},
            "devices": [{"name": "cuda:0 NVIDIA GeForce RTX 4070 : cudaMallocAsync",
                         "vram_total": 12 * 2**30, "vram_free": 5 * 2**30,
                         "torch_vram_total": torch_total, "torch_vram_free": 2**28}]}


def test_models_in_dedupes_and_ignores_text():
    found = backends._models_in(PROMPT)
    assert [m["name"] for m in found] == ["juggernautXL.safetensors", "detail.safetensors",
                                         "sdxl_vae.safetensors"]
    assert [m["kind"] for m in found] == ["ckpt", "lora", "vae"]


def test_models_in_tolerates_junk():
    assert backends._models_in(None) == []
    assert backends._models_in({"1": "x", "2": {"inputs": None}}) == []


def test_comfy_down(monkeypatch):
    monkeypatch.setattr(backends, "COMFY", "http://127.0.0.1:9")
    assert backends.comfy() == {"up": False}


def test_comfy_last_job(monkeypatch):
    hist = {"abc": {"prompt": [3, "abc", PROMPT, {}, ["9"]],
                    "status": {"status_str": "success",
                               "messages": [["execution_start", {"timestamp": 1_700_000_000_000}],
                                            ["execution_success", {"timestamp": 1_700_000_005_000}]]}}}
    srv, url = _server({"/system_stats": _stats(6 * 2**30),
                        "/queue": {"queue_running": [], "queue_pending": []},
                        "/history?max_items=1": hist})
    try:
        monkeypatch.setattr(backends, "COMFY", url)
        c = backends.comfy()
    finally:
        srv.shutdown()
    assert c["up"] and c["resident"] and c["held"] == 6 * 2**30
    assert c["device"] == "cuda:0 NVIDIA GeForce RTX 4070"
    assert c["models_from"] == "last" and c["last_job_at"] == 1_700_000_005
    assert c["models"][0]["name"] == "juggernautXL.safetensors"


def test_comfy_running_job_wins(monkeypatch):
    running = [[7, "r1", {"1": {"class_type": "UNETLoader",
                                "inputs": {"unet_name": "flux1-dev.gguf"}}}, {}, []]]
    srv, url = _server({"/system_stats": _stats(2**20),
                        "/queue": {"queue_running": running, "queue_pending": [[8], [9]]}})
    try:
        monkeypatch.setattr(backends, "COMFY", url)
        c = backends.comfy()
    finally:
        srv.shutdown()
    assert c["models_from"] == "running" and c["running"] == 1 and c["pending"] == 2
    assert c["models"] == [{"kind": "unet", "name": "flux1-dev.gguf", "node": "UNETLoader"}]
    assert not c["resident"]                      # 1 MB held = a warm context, not a model


def test_kobold_loaded(monkeypatch, tmp_path):
    gguf = tmp_path / "Qwen3-8B-Q4_K_M.gguf"
    gguf.write_bytes(b"not really a gguf")
    srv, url = _server({"/api/extra/true_max_context_length": {"value": 8192},
                        "/api/extra/version": {"result": "KoboldCpp", "version": "1.99"}})
    fake_inv = {"running": "Qwen3-8B-Q4_K_M", "active": gguf.name, "source": "model.env",
                "active_present": True, "gpu_layers": "30", "restart_needed": False,
                "models": [{"name": gguf.name, "size": 17, "path": str(gguf), "running": True}]}
    try:
        monkeypatch.setattr(backends, "KOBOLD", url)
        monkeypatch.setattr(inventory, "kobold", lambda: fake_inv)
        k = backends.kobold()
    finally:
        srv.shutdown()
    assert k["up"] and k["loaded"] == "Qwen3-8B-Q4_K_M" and k["file"] == gguf.name
    assert k["context"] == 8192 and k["version"] == "1.99" and k["gpu_layers"] == "30"


def test_kobold_down(monkeypatch):
    monkeypatch.setattr(inventory, "kobold", lambda: {"running": None, "active": "x.gguf",
                                                      "models": []})
    k = backends.kobold()
    assert k["up"] is False and k["selected"] == "x.gguf"


@pytest.mark.parametrize("module", ["stack_api.py", "models_api.py"])
def test_bff_modules_hold_no_state(module):
    """pytincture re-executes BFF modules per call: no module-level containers or calls."""
    import ast

    tree = ast.parse((Path(__file__).resolve().parents[1] / "appcode" / "services" / module)
                     .read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            raise AssertionError(f"{module}: module-level assignment at line {node.lineno}")
        if isinstance(node, ast.Expr) and not isinstance(node.value, ast.Constant):
            raise AssertionError(f"{module}: module-level call at line {node.lineno}")
