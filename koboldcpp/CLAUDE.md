# KoboldCPP

Containerized KoboldCPP LLM inference server, managed via Podman. NVIDIA CUDA on the
workstation; Vulkan or ROCm on the AMD Halo box (`../HALO_PLAN.md`). Which one is the **host
profile** (`../lib/host.sh`, optional `../host.env`): with no `host.env` the 4070 is detected
and nothing differs from before (verified 2026-10-30: identical `podman run` config).

## Structure

- `Containerfile` — CUDA: the official KoboldCPP Linux binary on CUDA 12.4 → `localhost/koboldcpp:latest`
- `Containerfile.vulkan` — the official `-nocuda` binary + Mesa RADV → `:vulkan` (0.5 GB). Also
  carries `libxext6`, without which NVIDIA's CDI-injected Vulkan driver is skipped. On the 4070
  it still only finds llvmpipe (NVIDIA's ICD wants more libraries than CDI injects); it was
  smoke-tested end to end on that CPU device. Meant for AMD.
- `Containerfile.rocm` — **compiles** mainline KoboldCPP at the same tag with
  `make koboldcpp_hipblas LLAMA_HIPBLAS=1 GPU_TARGETS=gfx1151` on `rocm/dev-ubuntu-24.04:7.14.1-full`
  (`ROCM_IMAGE` build arg) → `:rocm`, 20.5 GB. Upstream has no Linux ROCm binary. `--usecublas`
  selects the hipBLAS library. **No `--usemmap` on ROCm** (reported catastrophic on Strix Halo;
  off by default in this version — `--nommap` does not exist, don't add it).
- On AMD, `start.sh` uses `GPU_ARGS` from `lib/host.sh` (`/dev/kfd`, `/dev/dri`, the crun keep-groups annotation),
  defaults GPU layers to 999, uses an 8-bit KV cache (`--quantkv 1`; `KOBOLDCPP_QUANTKV=0` for f16), reads memory from amdgpu's sysfs (VRAM + GTT) instead of
  `nvidia-smi`, and skips the CUDA unified-memory retry.
- `start.sh` — launches the container; handles image build and cleanup automatically
- `models/` — GGUF model files mounted into the container at `/models`
- **Chat format is picked per model** by `start.sh` from the GGUF's own vocabulary/template (first
  64 MB), mounted at `/adapter.json` (2026-10-28 — a fixed `chatml.json` was wrong for Rocinante):
  | model file contains | adapter |
  |---|---|
  | `<|im_start|>` (Qwen, other ChatML) | `chatml.json` — per-role `<|im_start|>…<|im_end|>` |
  | `[SYSTEM_PROMPT]` (newer Mistral) | KoboldCPP's built-in `AutoGuess` |
  | `[INST]` only (Mistral **Nemo**, e.g. Rocinante) | `mistral-nemo.json` |
  | `[gMASK]<sop>` + `/nothink` + `<think></think>` (GLM-4.5/4.6 Air, e.g. Iceblink) | KoboldCPP's built-in `GLM-4-NoThink` (AutoGuess picks GLM-4.7's thinking format, whose replies start mid-reasoning with only a closing `</think>`) |
  | none of these | `AutoGuess` |
  `mistral-nemo.json` follows Nemo's template (`[INST]{system}\n\n{user}[/INST]{reply}</s>`, no spaces,
  no system tokens): system text joins the surrounding `[INST]` block, so the companion's trailing
  `system` notes land inside the last user turn right before `[/INST]`. Nemo's jinja template rejects
  any system message but the first, which is why `--jinja` stays off; KoboldCPP's own Mistral
  adapters don't fit Nemo either (`Mistral-Tekken` = V7 `[SYSTEM_PROMPT]` tokens Nemo lacks,
  `Mistral-NonTekken` = old spaced format). Under ChatML, Rocinante — which has no ChatML tokens —
  occasionally wrote `<|im_end|>` as text and then a turn for the user. Override with
  `KOBOLDCPP_ADAPTER` (a file here or a built-in name). Qwen models are heavy `<think>` reasoners —
  the companion strips `<think>…</think>` client-side.

## Starting the server

```bash
~/Development/ai_ecosystem/koboldcpp/start.sh
```

The container is **`koboldcpp-local`** and publishes no host port: the backend router (`ai-stack/router/`) answers at http://localhost:5001 and as `koboldcpp` on ai-net, and forwards here while text is routed local (`stack route`). The container runs with `--restart=on-failure:5`: podman restarts it after a crash, but not after `podman stop`. A stopped container lingers (logs intact) until the script runs again and replaces it.

### What a (re)start does — hardened 2026-10-28 after a model swap failed to load

1. Stops the old container cleanly and **waits for its VRAM to be released** (it frees after
   `podman rm` returns, not by it).
2. **Asks ComfyUI to drop its cached models** (`POST :8188/free`; it reloads on its next job).
   Measured on this box: memory another process holds shrinks what a new process can allocate by
   about twice its size — 8.7 GB reported free, only 5.4 GB allocatable with ComfyUI holding 2.3 GB,
   9.5 GB once it let go. That is why Rocinante 12B (needs ~7 GB at 33 layers) failed after a swap
   while `nvidia-smi` showed plenty free. `KOBOLDCPP_FREE_COMFY=0` skips this.
4. **Waits for the load** (`Load Text Model OK: True`, up to `KOBOLDCPP_LOAD_TIMEOUT`=300 s) instead
   of returning at once. On an out-of-memory failure:
   - **Fragmented VRAM → same layers in CUDA unified memory.** The desktop apps' small GPU buffers
     sit in the middle of VRAM, so "free" can be large while no single block is: measured 10.4 GB
     free, largest block ~5 GB, and 33-layer Rocinante wants one 5.5 GB weights buffer.
     `GGML_CUDA_ENABLE_UNIFIED_MEMORY=1` doesn't need one block — it loaded at 33 layers, 23–32
     tok/s, vs 14–17 tok/s after dropping to 29. Used **only** when the failed attempt's log shows
     `free >= weights buffer + KOBOLDCPP_UM_MARGIN` (2500 MiB): unified memory never fails on a
     real shortage, it spills to system RAM and crawls.
   - **Real shortage → `KOBOLDCPP_LAYER_STEP`=4 fewer GPU layers** per retry, with a warning naming
     the count that worked (stderr, which `stack restart` shows).
   Before this, the restart policy repeated the identical OOM five times and `stack` only said "did
   not come up". A non-memory failure exits 1 with the last log lines.

### Release and restore: admin mode (since jp-parent#50)

KoboldCPP holds its whole allocation while idle (~5.9 GB for a 9B Q4 on the 4070), and a heavy
ComfyUI job (FLUX schnell Q4 ≈ 6.8 GB, Kontext ≈ 12.5 GB) needs it back. `stack gpu-release` frees
it and `stack gpu-restore` loads the same model again, without replacing the container:

- **`--admin`**: the KoboldCPP process becomes a supervisor that runs the model in a child
  process. `POST /api/admin/reload_config {"filename": "unload_model"}` ends that child (every
  byte of its VRAM is freed) and starts one with no model; `"initial_model"` starts it again with
  the container's own arguments (model, layers, context, adapter, the unified-memory setting a
  fallback may have picked). A restore is a model load, not a boot: no container, no image, none
  of this script's checks. Measured in 1.111.2's `koboldcpp.py`; a failed first load still exits
  the container, so the OOM retries above work as before. A failed *restore* is retried once
  with the original arguments by KoboldCPP itself, then the container exits.
- **Locked to those two requests.** The admin folder is an empty tmpfs (`/kcpp-admin`, read-only),
  so no `.kcpps` or `.gguf` can be named to swap to; and `KCPP_ADMINPASSWORD` is random on every
  start and exists only in the container's environment. Port 5001 is on ai-net and behind the
  router's host port, so without the password anything there could unload the model. `stack`
  reaches it with `podman exec koboldcpp-local curl … 127.0.0.1:5001` (curl is in all three images).
- **Released, it still answers.** `/api/v1/model` says `koboldcpp/inactive` with a 200, so
  `stack`'s `is_up` and the local probe treat that as not up, `local_state` reports `released`,
  and `stack up` (or `stack koboldcpp`) restores rather than rebuilding the container.
- `gpu-restore` asks ComfyUI to free its cache first, as a start does (`KOBOLDCPP_FREE_COMFY=0`
  skips it). It doesn't retry with fewer layers: on failure it says to `stack restart koboldcpp`,
  which does.
- `KOBOLDCPP_ADMIN=0` starts without admin mode. A container started that way, or before this
  existed, can't unload: `gpu-release` stops it instead, and `gpu-restore` starts it (a full start).

## Configuration

Resolved by `start.sh` as **env var → `model.env` → built-in default**:

| Variable | Default | Description |
|---|---|---|
| `KOBOLDCPP_MODEL` | `Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` | Model file in `models/` |
| `KOBOLDCPP_GPU_LAYERS` | `33` | Number of layers to offload to GPU |
| `KOBOLDCPP_ADAPTER` | picked from the model file | chat format: `chatml.json`, `mistral-nemo.json`, or a KoboldCPP built-in (`AutoGuess`, `Llama-3`, …) |
| `KOBOLDCPP_SMARTCACHE` | `on` | `--smartcache`: KV snapshots in RAM, so an app's side call doesn't make the next chat turn re-read the whole conversation (~12 s → ~2 s to first token, measured). `0` turns it off, a number sets the slot limit |
| `KOBOLDCPP_CONTEXT` | unset (KoboldCPP default, 8192) | `--contextsize`; one of 4096 6144 8192 12288 16384 24576 32768 49152 65536 98304 131072 (48K+ sized for the AMD box's unified memory), anything else refuses to start. KV cache grows with it. Apps read the live value from `/api/extra/true_max_context_length` (Pantheon budgets against it). Set it from AI Ecosystem → Language models → Context window (shows each size's KV-cache cost), or by hand; stack-ui keeps it when it rewrites `model.env` |

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
frontends — companion, story, iguana — reach it by name at `http://koboldcpp:5001`. That name is
the **backend router's** network alias; the router forwards to this container (`koboldcpp-local`)
or, later, to the Halo box (`HALO_PLAN.md`).

## Install

Building this from scratch on a new machine: [`INSTALL.md`](INSTALL.md).

## Updating KoboldCPP version

Edit the `ARG KOBOLDCPP_VERSION` line in `Containerfile` **and** in `Containerfile.vulkan` /
`Containerfile.rocm` (keep them on one release), then rebuild:

```bash
podman rmi localhost/koboldcpp:latest
~/Development/ai_ecosystem/koboldcpp/start.sh
```
