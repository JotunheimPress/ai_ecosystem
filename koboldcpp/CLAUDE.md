# KoboldCPP

Containerized KoboldCPP LLM inference server with NVIDIA CUDA support, managed via Podman.

## Structure

- `Containerfile` — builds the image from the official KoboldCPP Linux binary on CUDA 12.4 base
- `start.sh` — launches the container; handles image build and cleanup automatically
- `models/` — GGUF model files mounted into the container at `/models`
- `chatml.json` — ChatCompletions adapter (per-role `<|im_start|>…<|im_end|>` tags) passed via
  `--chatcompletionsadapter /chatml.json`, so `/v1/chat/completions` formats **ChatML** deterministically
  (the current Qwen3.5 model expects it) instead of relying on AutoGuess. Per-role tags render the
  companion's trailing `system` (post-history) message cleanly; `--jinja` was avoided because a strict
  Qwen template can mishandle a late system turn. Note: this model is a heavy `<think>` reasoner — the
  companion strips `<think>…</think>` client-side.

## Starting the server

```bash
~/Development/ai_ecosystem/koboldcpp/start.sh
```

Runs at http://localhost:5001. The container runs with `--restart=on-failure:5`: podman restarts it after a crash, but not after `podman stop`. A stopped container lingers (logs intact) until the script runs again and replaces it.

### What a (re)start does — hardened 2026-10-28 after a model swap failed to load

1. Stops the old container cleanly and **waits for its VRAM to be released** (it frees after
   `podman rm` returns, not by it).
2. **Asks ComfyUI to drop its cached models** (`POST :8188/free`; it reloads on its next job).
   Measured on this box: memory another process holds shrinks what a new process can allocate by
   about twice its size — 8.7 GB reported free, only 5.4 GB allocatable with ComfyUI holding 2.3 GB,
   9.5 GB once it let go. That is why Rocinante 12B (needs ~7 GB at 33 layers) failed after a swap
   while `nvidia-smi` showed plenty free. `KOBOLDCPP_FREE_COMFY=0` skips this.
3. **Waits for the load** (`Load Text Model OK: True`, up to `KOBOLDCPP_LOAD_TIMEOUT`=300 s) instead
   of returning at once. On an out-of-memory failure it retries with `KOBOLDCPP_LAYER_STEP`=4 fewer
   GPU layers, up to 4 times, and prints a warning naming the layer count that worked (stderr, which
   `stack restart` shows). Before this, the restart policy repeated the identical OOM five times and
   `stack` only said "did not come up". A non-memory failure exits 1 with the last log lines.

## Configuration

Resolved by `start.sh` as **env var → `model.env` → built-in default**:

| Variable | Default | Description |
|---|---|---|
| `KOBOLDCPP_MODEL` | `Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` | Model file in `models/` |
| `KOBOLDCPP_GPU_LAYERS` | `33` | Number of layers to offload to GPU |

```bash
# one-off (doesn't persist)
KOBOLDCPP_MODEL=other.gguf KOBOLDCPP_GPU_LAYERS=20 ~/Development/ai_ecosystem/koboldcpp/start.sh

# persistent selection — survives stack up / reboot, which an env var does not
printf 'KOBOLDCPP_MODEL=other.gguf\nKOBOLDCPP_GPU_LAYERS=28\n' > ~/Development/ai_ecosystem/koboldcpp/model.env
stack restart koboldcpp
```

`model.env` is **parsed, not sourced** (machine-written from internet-sourced model names), lives in
the working tree next to `models/` as runtime state, and is the hook a control UI writes to when
switching models. A model it names that isn't in `models/` fails the start with the available list
instead of crash-looping inside the container. Full rationale: [`INSTALL.md`](INSTALL.md) §4.

## Networking

Connected to the **`ai-net`** Podman network (renamed from the legacy `sillytavern-net`), so the
frontends — companion, story, iguana — reach it by name at `http://koboldcpp:5001`.

## Install

Building this from scratch on a new machine: [`INSTALL.md`](INSTALL.md).

## Updating KoboldCPP version

Edit the `ARG KOBOLDCPP_VERSION` line in `Containerfile`, then rebuild:

```bash
podman rmi localhost/koboldcpp:latest
~/Development/ai_ecosystem/koboldcpp/start.sh
```
