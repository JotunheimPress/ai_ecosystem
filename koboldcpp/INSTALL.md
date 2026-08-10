# Installing the KoboldCPP backend

The full recipe lives in this folder — `Containerfile`, `start.sh`, `chatml.json`,
`.containerignore`. Unlike ComfyUI (an upstream checkout you build yourself), **KoboldCPP here is a
self-contained container recipe**: point `start.sh` at a directory with a GGUF model in `models/` and
it builds the image on first run and starts the server on `:5001`.

## Reference environment

| | |
|---|---|
| Host | Bazzite DX (Fedora 44 / Kinoite), immutable |
| GPU | NVIDIA RTX 4070, **12 GB VRAM**, driver 610.43.03 |
| Base image | `nvidia/cuda:12.4.1-runtime-ubuntu22.04` |
| KoboldCPP | `v1.111.2` (pinned in `Containerfile`) |
| Image size | ~2.9 GB built |

## Host prerequisites

Podman and the NVIDIA container toolkit (`nvidia-ctk`) — the container is started with
`--device nvidia.com/gpu=all`, which requires CDI to be configured. Verify `nvidia-smi` works on the
host first. No distrobox needed: unlike ComfyUI, this backend is a plain container.

## 1. Lay out the project directory

`start.sh` resolves everything relative to its own location, so keep this shape:

```
koboldcpp/
├── Containerfile        # from this folder
├── start.sh             # from this folder
├── chatml.json          # from this folder
├── .containerignore     # from this folder — see the warning below
└── models/              # YOUR GGUF files (not vendored)
```

Copy the four files out of this repo, then create `models/` alongside them.

> **Don't drop the `.containerignore`.** `start.sh` builds with the project directory as its build
> context, so without it Podman tars the entire `models/` tree — ~15 GB of GGUFs — on every image
> build. The file excludes `models/` and `*.gguf`. This bites only on a fresh build, which is exactly
> what a first-time install is.

## 2. Get a model

Any GGUF works. Drop it in `models/` and point `KOBOLDCPP_MODEL` at the filename. On **12 GB VRAM**,
target a ≤13B model at Q4–Q5 quantization. What's in use here:

| Model | Size | Note |
|---|---|---|
| `Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` | 5.3 G | the default in `start.sh` |
| `GLM-4.7-Flash-Uncen-Hrt-NEO-CODE-MAX-imat-D_AU-IQ2_M.gguf` | 9.7 G | larger alternative |

Both are community quantizations from Hugging Face — search by filename. Weights are not vendored.

## 3. Start it

```bash
./start.sh
```

First run builds the image (a few minutes: it pulls the CUDA base and downloads the KoboldCPP
binary), then starts detached. Subsequent runs reuse the image and start in seconds; model load takes
another ~20–30 s. Web UI and API at http://localhost:5001.

```bash
./start.sh --help                     # KoboldCPP CLI options (passed straight through)
podman logs -f koboldcpp              # follow startup / model load
curl -s localhost:5001/api/v1/model   # confirm which model is live
```

The container runs with `--rm`, so it vanishes when stopped — just run the script again.

## 4. Configuration

Both knobs are environment variables read by `start.sh`:

| Variable | Default | Description |
|---|---|---|
| `KOBOLDCPP_MODEL` | `Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` | filename inside `models/` |
| `KOBOLDCPP_GPU_LAYERS` | `33` | layers offloaded to GPU; lower it if you OOM |

```bash
KOBOLDCPP_MODEL=other.gguf KOBOLDCPP_GPU_LAYERS=20 ./start.sh
```

`--usecublas` is always passed. If a model won't fit, reduce `KOBOLDCPP_GPU_LAYERS` rather than
switching off CUDA — partial offload is much faster than pure CPU.

## 5. The ChatML adapter

`chatml.json` is mounted at `/chatml.json` and passed via `--chatcompletionsadapter`, which makes
`/v1/chat/completions` emit **ChatML** (`<|im_start|>…<|im_end|>`) deterministically instead of
relying on KoboldCPP's AutoGuess. It matters for two reasons:

- The Qwen3.5 model expects ChatML, and AutoGuess is not reliable enough to depend on.
- Per-role tags render the companion's trailing post-history `system` message cleanly. `--jinja` was
  deliberately avoided: a strict Qwen template can mishandle a late system turn.

Using a model with a different prompt format? Replace `chatml.json` or drop the
`--chatcompletionsadapter` flag from `start.sh`.

> **Thinking models:** the current Qwen3.5 build is a heavy `<think>` reasoner. Clients are expected
> to strip `<think>…</think>` — the companion does this client-side. Budget for the extra tokens.

## 6. Networking

`start.sh` attaches the container to the **`ai-net`** Podman network and publishes `5001:5001`:

- from the host → `localhost:5001`
- from other containers on `ai-net` → `http://koboldcpp:5001` (by container name)

Create the network once if it doesn't exist — `stack` does this automatically:

```bash
podman network create ai-net
```

## 7. Updating the KoboldCPP version

Versions are pinned in the `Containerfile`. Bump the ARG and rebuild:

```bash
# edit ARG KOBOLDCPP_VERSION in Containerfile (tags: github.com/LostRuins/koboldcpp/releases)
podman rmi localhost/koboldcpp:latest
./start.sh                            # rebuilds automatically
```

## VRAM budget

12 GB total, **shared with ComfyUI**. A 9B model at Q4 with 33 layers offloaded leaves room for an
SD 1.5 checkpoint, but not SDXL. Running both heavy models at once will OOM — see
[`../comfyui/INSTALL.md`](../comfyui/INSTALL.md). `stack off` stops both and frees the GPU for gaming.

## Note on this folder

These files are the canonical copies. In the working tree,
`~/Development/ai_ecosystem/koboldcpp/` symlinks back to them, so the running stack and the published
recipe can't drift apart. `models/` stays in the working directory and is never committed.
