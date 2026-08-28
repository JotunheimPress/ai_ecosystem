# Installing the ComfyUI backend

ComfyUI as a **plain Podman container** on `ai-net`, pinned at `v0.31.1`, listening on `:8188`.
Same shape as the KoboldCPP backend: `Containerfile` + `start.sh`, image built on first run.

> **This used to be a distrobox.** It isn't any more — nothing here needed distrobox, and the
> container removes a whole class of problems (see [Why not distrobox](#why-not-distrobox)). The old
> `comfyui` distrobox container was renamed `comfyui-distrobox-old` rather than deleted; remove it
> with `podman rm comfyui-distrobox-old` once you're happy, and `distrobox rm` its leftovers.

## Reference environment

| | |
|---|---|
| Host | Bazzite DX (Fedora 44 / Kinoite), immutable |
| GPU | NVIDIA RTX 4070, **12 GB VRAM**, driver 610.43.03 |
| Base image | `ubuntu:24.04` (no CUDA base needed — see below) |
| Python | 3.12 (venv at `/opt/venv` inside the image) |
| torch | **2.11.0+cu130** (CUDA 13.0) |
| ComfyUI | tag `v0.31.1`, cloned into `/opt/ComfyUI` |
| Image size | ~6.9 GB built |

No CUDA base image: the cu130 torch wheels bundle the CUDA runtime, and the driver is injected by
CDI (`--device nvidia.com/gpu=all`). KoboldCPP needs a CUDA base because it ships a compiled binary.

## Host prerequisites

Podman and the NVIDIA container toolkit (`nvidia-ctk`) with a CDI spec at `/etc/cdi/nvidia.yaml`.
Verify before starting:

```bash
nvidia-smi                # driver works on the host
nvidia-ctk cdi list       # expect nvidia.com/gpu=all
```

On an immutable host **do not `dnf install`** — Podman is already present on Bazzite DX and
`nvidia-ctk` ships with the `-nvidia` image variant.

## 1. Layout

Code lives in the image. The host checkout at `~/Development/ai_ecosystem/ComfyUI/` supplies only
**mutable state**, bind-mounted under `/data` and selected with `--base-directory /data`:

| Host | Container | Size here |
|---|---|---|
| `ComfyUI/models` | `/data/models` | 58 G |
| `ComfyUI/output` | `/data/output` | 594 M |
| `ComfyUI/input` | `/data/input` | 7.2 M |
| `ComfyUI/user` | `/data/user` | 312 K — settings, workflows, `comfyui.db` |
| `ComfyUI/custom_nodes` | `/data/custom_nodes` | 16 K — stock only |

That checkout's own `venv/` and Python source are no longer used by the stack. Keep it for the state
dirs (and as a reference checkout), or thin it out — but don't delete `models/`, `user/`, `output/`.

## 2. Build and run

```bash
~/Development/ai_ecosystem/ai-stack/comfyui/start.sh     # builds on first run, then starts detached
```

Under the stack, prefer `stack comfyui` / `stack up`. Cold start is ~5 s; the first build takes
several minutes (torch + CUDA wheels are most of the 6.9 GB).

```bash
podman logs -f comfyui                        # or: stack logs comfyui
curl -s localhost:8188/system_stats | jq .devices   # confirm the GPU is seen
```

## 3. Configuration

| Variable | Default | Description |
|---|---|---|
| `COMFYUI_STATE` | `$AI_ECOSYSTEM/ComfyUI` | host dir holding the state subdirs |
| `COMFYUI_PORT` | `8188` | published host port |
| `COMFYUI_NET` | `ai-net` | Podman network to join |
| `COMFYUI_NAME` | `comfyui` | container name; override to run a second copy |

Extra flags pass straight to `main.py`: `./start.sh --lowvram`.

## 4. The two flags that matter

**`--security-opt label=disable`** — without it SELinux denies the container access to the
CDI-injected `/dev/nvidia*` nodes. The failure is confusing rather than obvious: `nvidia-smi` inside
the container reports `Failed to initialize NVML: Insufficient Permissions`, and ComfyUI dies later
with `RuntimeError: No CUDA GPUs are available` while otherwise looking healthy. This is narrower
than the `--privileged` KoboldCPP uses — capability confinement stays on.

**No `:z` on the volumes** — with SELinux labelling disabled for the container, relabeling is
redundant, and `:z` would walk the entire 58 GB `models/` tree on every single start.

## 5. Verify the GPU is actually visible

Don't skip this — an install that silently falls back to CPU looks fine until first generation:

```bash
curl -s localhost:8188/system_stats | jq '.devices[0]'
# expect: "NVIDIA GeForce RTX 4070", type "cuda", vram_total ~12.5e9
```

## 6. Boot auto-start

Handled by the stack's single switch — `stack on` / `stack off` via `ai-stack-backends.service`.
See [`../CLAUDE.md`](../CLAUDE.md).

> **Do not re-enable the old standalone `comfyui.service`.** It's disabled on purpose: it competed
> with the stack for `:8188` and undid `stack down` via `Restart=on-failure`. The stack is the sole
> owner of ComfyUI.

## 7. Updating ComfyUI

Version is pinned in the `Containerfile`. Bump the ARG and rebuild:

```bash
# edit ARG COMFYUI_VERSION (tags: github.com/comfyanonymous/ComfyUI/releases)
podman rmi localhost/comfyui:latest
~/Development/ai_ecosystem/ai-stack/comfyui/start.sh     # rebuilds automatically
```

`requirements.lock.v0.31.1.txt` is COPYed into the build and pins the environment. A version bump
generally needs a matching lock — regenerate with `pip freeze` inside a built image.
`requirements.lock.pre-0.31.txt` is the rollback artifact for the pre-v0.31 state; not a target.

## 8. Models — the part you have to bring yourself

Model weights are third-party, licensed separately, and total ~58 GB here. They are not vendored and
never should be. Download into `ComfyUI/models/checkpoints/` on the **host** — the bind mount picks
them up without a rebuild.

**You do not need all of these.** For the companion's sprite pipeline running *concurrently with
KoboldCPP* on 12 GB VRAM, the practical minimum is a single SD 1.5 checkpoint — start with
`absolutereality_v181` (2 GB) and add SDXL models only if you have the headroom.

| Checkpoint | Size | Base | Source |
|---|---|---|---|
| `absolutereality_v181` | 2.0 G | SD 1.5 | Civitai — **start here** |
| `v1-5-pruned-emaonly-fp16` | 2.0 G | SD 1.5 | Hugging Face (SD 1.5 archive) |
| `sd_xl_base_1.0` | 6.5 G | SDXL | Hugging Face — `stabilityai/stable-diffusion-xl-base-1.0` |
| `dreamshaperXL_lightningDPMSDE` | 6.5 G | SDXL | Civitai |
| `juggernautXL_ragnarokBy` | 6.7 G | SDXL | Civitai |
| `realvisxlV50_v50LightningBakedvae` | 6.5 G | SDXL | Civitai |
| `ponyDiffusionV6XL_v6StartWithThisOne` | 6.5 G | SDXL | Civitai |
| `talmendoxlSDXL_v11Beta` | 6.5 G | SDXL | Civitai |
| `crealismTerraMirabilis_v2025Steps` | 6.7 G | SDXL | Civitai |
| `secuanceDreamDeliberate_v125Main` | 4.0 G | SD 1.5 | Civitai |

Search Civitai by the filename above; exact URLs are omitted because they move. Other populated
subtrees: `photomaker/` (2.6 G), `insightface/` (927 M), `vae/` (320 M) — only needed for the
workflows that reference them.

### Video — WAN 2.2 TI2V 5B

Only needed for Iguana Studio's **Animate** mode (image→video) and `run.py video`. Unlike the
checkpoints above these are **not** interchangeable: all three go together, and the filenames are
what `iguana_studio/comfyui-auto/workflows/video.py` names.

From `Comfy-Org/Wan_2.2_ComfyUI_Repackaged` on Hugging Face, under `split_files/`:

| File | Size | Goes in |
|---|---|---|
| `wan2.2_ti2v_5B_fp16.safetensors` | 9.3 G | `models/diffusion_models/` |
| `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | 6.3 G | `models/text_encoders/` |
| `wan2.2_vae.safetensors` | 1.3 G | `models/vae/` |

No custom nodes: ComfyUI v0.31.1 has native WAN nodes, native `CreateVideo`/`SaveVideo`, and the
container's PyAV already carries an h264 encoder. Nothing to rebuild — the bind mount picks the
weights up, and `UNETLoader` lists the model on the next `/object_info` call.

**If fp16 will not fit**, put a GGUF WAN 5B (Q5_K_M, ~4 G) in `models/unet/` instead and swap node
`1` in `video.py` from `UNETLoader` to `UnetLoaderGGUF`. `ComfyUI-GGUF` is already a pinned custom
node (it is what loads Flux Kontext), so nothing else changes.

## VRAM budget

12 GB total, **shared with KoboldCPP**. Running a large LLM and an SDXL checkpoint at once will OOM.
Either run one heavy model at a time, or pair KoboldCPP with an SD 1.5 checkpoint. `stack off` frees
the GPU entirely for gaming.

**WAN 2.2 5B wants the card to itself.** At fp16 it is ~10 GB of the 11.6 GiB the driver reports, so
`stack down koboldcpp` (or the dashboard's **Free VRAM**) before using Animate. Measured here with
the card free: 25 frames at 960×544, 20 steps → 41 s.

## Topology

ComfyUI is a container on **`ai-net`**, published to the host:

- from the host → `localhost:8188`
- from containers on `ai-net` (companion, story, iguana) → **`http://comfyui:8188`** (by name)

This matches how KoboldCPP is reached (`http://koboldcpp:5001`). The old
`host.containers.internal:8188` still works while the port is published, but the frontends now
default to the by-name form.

## Why not distrobox

The original install ran in a `--nvidia` distrobox. Nothing required it — `custom_nodes/` is stock,
and CDI passes the GPU into a plain container just as well. Moving to a container removed:

- a `kind=proc` special case throughout `stack` (stop-by-port-PID, no `podman stop`)
- a `distrobox enter` invocation that hung the caller unless detached with `-T` + `setsid`
- hand-rolled log append/rotation, replaced by `podman logs`
- a slow cold start (~30 s+) — now ~5 s
- the venv-interpreter trap below

> **The trap this fixed.** `ComfyUI/venv/bin/python3` on the host resolves to `/usr/bin/python3.14`
> — the *Fedora host* interpreter, not the Ubuntu 3.12 the venv was built against — because the venv
> lived in a host-visible checkout and symlinks the system python. Running it from the host silently
> picked up the wrong interpreter (`No module named pip`, missing packages). Dependencies now live in
> the image, so this can't happen.
