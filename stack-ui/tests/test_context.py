"""KoboldCPP's context window: model.env writes, restart detection, KV-cache estimate.

    .venv/bin/python -m pytest -q tests/test_context.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "appcode"))

from services import inventory, vram  # noqa: E402

START_SH = Path(__file__).resolve().parents[2] / "koboldcpp" / "start.sh"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "a.gguf").write_bytes(b"x")
    (models / "b.gguf").write_bytes(b"x")
    monkeypatch.setattr(inventory, "MODEL_ENV", tmp_path / "model.env")
    monkeypatch.setattr(inventory, "KOBOLD_MODELS", models)
    monkeypatch.setitem(inventory.WRITABLE, "kobold", (models, inventory.WRITABLE["kobold"][1]))
    monkeypatch.setattr(inventory, "PRESETS", tmp_path / "presets.json")
    monkeypatch.setattr(inventory, "running_model", lambda: None)
    monkeypatch.setattr(inventory, "running_context", lambda: None)
    return tmp_path / "model.env"


def test_sizes_match_start_sh():
    m = re.search(r"^\s*([\d|]+)\) CONTEXT_ARGS", START_SH.read_text(), re.M)
    assert tuple(int(x) for x in m.group(1).split("|")) == inventory.CONTEXT_SIZES


def test_set_context_keeps_every_other_line(env):
    env.write_text("# hand note\nKOBOLDCPP_MODEL=a.gguf\nKOBOLDCPP_GPU_LAYERS=30\n"
                   "KOBOLDCPP_ADAPTER=chatml.json\n")
    k = inventory.set_kobold_context(16384)
    text = env.read_text()
    assert "KOBOLDCPP_CONTEXT=16384" in text and "KOBOLDCPP_ADAPTER=chatml.json" in text
    assert "# hand note" in text and "KOBOLDCPP_GPU_LAYERS=30" in text
    assert k["context"] == 16384
    inventory.set_kobold_context(12288)
    assert env.read_text().count("KOBOLDCPP_CONTEXT") == 1
    inventory.set_kobold_context(None)                    # back to KoboldCPP's default
    assert "KOBOLDCPP_CONTEXT" not in env.read_text()
    assert inventory.kobold()["context"] is None


def test_set_context_without_a_model_env(env):
    inventory.set_kobold_context(8192)
    assert env.read_text().splitlines()[-1] == "KOBOLDCPP_CONTEXT=8192"


def test_only_standard_sizes(env):
    with pytest.raises(inventory.InventoryError):
        inventory.set_kobold_context(10000)
    assert not env.exists()


def test_choosing_a_model_keeps_the_context(env):
    inventory.set_kobold_context(24576)
    inventory.select_kobold("b.gguf", None)
    assert "KOBOLDCPP_CONTEXT=24576" in env.read_text()


def test_restart_needed_for_a_context_change(env, monkeypatch):
    env.write_text("KOBOLDCPP_MODEL=a.gguf\nKOBOLDCPP_CONTEXT=16384\n")
    monkeypatch.setattr(inventory, "running_model", lambda: "a")
    monkeypatch.setattr(inventory, "running_context", lambda: 8192)
    k = inventory.kobold()
    assert k["restart_needed"] and k["restart_for"] == ["context"]
    monkeypatch.setattr(inventory, "running_context", lambda: 16384)
    assert not inventory.kobold()["restart_needed"]


def test_unset_context_means_the_default(env, monkeypatch):
    env.write_text("KOBOLDCPP_MODEL=a.gguf\n")
    monkeypatch.setattr(inventory, "running_model", lambda: "a")
    monkeypatch.setattr(inventory, "running_context", lambda: 8192)
    assert not inventory.kobold()["restart_needed"]
    monkeypatch.setattr(inventory, "running_context", lambda: 16384)
    assert inventory.kobold()["restart_for"] == ["context"]


def test_kv_cache_estimate():
    nemo = {"n_layers": 40, "n_heads": 32, "n_kv_heads": 8, "key_len": 128, "value_len": 128}
    assert vram.kv_cache_bytes(nemo, 8192) == 1280 * 2**20            # 1.25 GB, f16
    assert vram.kv_cache_bytes(nemo, 16384) == 2 * vram.kv_cache_bytes(nemo, 8192)
    hybrid = {**nemo, "attn_interval": 4}                            # Qwen3.5: 1 layer in 4
    assert vram.kv_cache_bytes(hybrid, 8192) == vram.kv_cache_bytes(nemo, 8192) // 4
    no_keylen = {"n_layers": 32, "n_heads": 32, "n_kv_heads": 8, "n_embd": 4096}
    assert vram.kv_cache_bytes(no_keylen, 4096) == 32 * 4096 * 8 * 256 * 2
    assert vram.kv_cache_bytes({"n_layers": 40}, 8192) is None        # shape unknown


def test_gguf_meta_reads_attention_shape(tmp_path):
    import struct

    def s(x: str) -> bytes:
        b = x.encode()
        return struct.pack("<Q", len(b)) + b

    kvs = [s("general.architecture") + struct.pack("<I", 8) + s("llama")]
    for k, v in (("block_count", 40), ("context_length", 131072), ("attention.head_count", 32),
                 ("attention.head_count_kv", 8), ("attention.key_length", 128)):
        kvs.append(s(f"llama.{k}") + struct.pack("<I", 4) + struct.pack("<I", v))
    kvs.append(s("tokenizer.ggml.model") + struct.pack("<I", 8) + s("gpt2"))
    f = tmp_path / "m.gguf"
    f.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, len(kvs)) + b"".join(kvs))
    meta = vram.gguf_meta(f)
    assert meta == {"arch": "llama", "n_layers": 40, "train_ctx": 131072, "n_heads": 32,
                    "n_kv_heads": 8, "key_len": 128}
