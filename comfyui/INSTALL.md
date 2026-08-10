# Installing the ComfyUI backend

How to reproduce the `comfyui` backend this stack expects: **stock ComfyUI, pinned at `v0.31.1`,
running in a GPU-passthrough distrobox on its own venv**, listening on `:8188`.

This documents *our delta* — the distrobox, the CUDA-specific torch install, the systemd unit, the
VRAM budget. For ComfyUI's own usage/UI docs see [upstream](https://github.com/comfyanonymous/ComfyUI);
this file deliberately doesn't restate them.

> **No custom nodes.** `custom_nodes/` holds only the two files ComfyUI ships with
> (`example_node.py.example`, `websocket_image_save.py`). That's what makes this install
> reproducible in a few minutes — there's no node-dependency tangle to untangle.

## Reference environment

What the lock files were captured from — the closer you match this, the fewer surprises:

| | |
|---|---|
| Host | Bazzite DX (Fedora 44 / Kinoite), immutable |
| GPU | NVIDIA RTX 4070, **12 GB VRAM**, driver 610.43.03 |
| Container | `ubuntu:24.04` via distrobox (Podman) |
| Python | 3.12.3 (distrobox system python) |
| torch | **2.11.0+cu130** (CUDA 13.0) |
| ComfyUI | tag `v0.31.1` |

## Host prerequisites

Podman, distrobox, and the NVIDIA container toolkit (`nvidia-ctk`) so the GPU passes into the
container. On an immutable host **do not `dnf install`** — distrobox and Podman are already present
on Bazzite DX; `nvidia-ctk` ships with the `-nvidia` image variant. Verify with `nvidia-smi` on the
host before starting.

## 1. Create the distrobox

```bash
distrobox create --nvidia --name comfyui --image ubuntu:24.04
distrobox enter comfyui
```

`--nvidia` is the part that matters — it wires the driver and container toolkit through. Everything
below runs **inside** the box.

## 2. System dependencies

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip build-essential \
                    libgl1 libglib2.0-0t64 libgomp1
```

`libgl1` / `libglib2.0-0t64` are needed by OpenCV, which several ComfyUI dependencies pull in; without
them you get an import error at startup rather than at install time.

## 3. Clone ComfyUI at the pinned tag

```bash
cd ~/Development/ai_ecosystem
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
git checkout v0.31.1        # detached HEAD — intentional, this is a pinned release
```

## 4. Create the venv and install torch **first**

This is the step that goes wrong most often. torch must come from the CUDA 13.0 index — the default
PyPI index gives you a *different* build that will not match this environment:

```bash
python3 -m venv venv
./venv/bin/python3 -m pip install --upgrade pip
./venv/bin/python3 -m pip install --index-url https://download.pytorch.org/whl/cu130 \
    torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0
./venv/bin/python3 -m pip install -r requirements.txt
```

To reproduce the full pinned environment exactly, use the lock **after** the torch step above:

```bash
./venv/bin/python3 -m pip install -r ~/Development/ai_ecosystem/ai-stack/comfyui/requirements.lock.v0.31.1.txt
```

Lock files in this folder:

- **`requirements.lock.v0.31.1.txt`** — the current working environment. Use this one.
- **`requirements.lock.pre-0.31.txt`** — the state we rolled back *from*, kept as a rollback
  artifact alongside the `pre-update-v0.18.1` branch. Not a target to install.

## 5. Verify the GPU is actually visible

Don't skip this — an install that silently falls back to CPU looks fine until first generation:

```bash
nvidia-smi                                    # inside the box
./venv/bin/python3 -c "import torch; print(torch.__version__, torch.cuda.is_available())"
# expected: 2.11.0+cu130 True
```

`False` means `--nvidia` didn't take; recreate the box rather than fighting it.

## 6. Run it

```bash
cd ~/Development/ai_ecosystem/ComfyUI
./venv/bin/python3 main.py --listen 0.0.0.0 --port 8188
```

UI/API at http://localhost:8188. Under the stack, prefer `stack comfyui` / `stack up`.

> **Gotcha — run it from inside the distrobox.** The venv is Python **3.12** from Ubuntu, while a
> Fedora host has its own (3.14) `/usr/bin/python3`. `venv/bin/python3` is a symlink to the *system*
> python, so invoking it from the host silently picks up the wrong interpreter and fails with
> `No module named pip` or missing packages. Test with
> `podman exec --user oldmangan comfyui bash -lc '…'`, not on the host.

## 7. Boot auto-start (optional)

`comfyui.service` (systemd **user** unit) starts it after the distrobox is reachable. It waits for
the container, then launches the venv python directly:

```ini
ExecStartPre=/bin/bash -c 'until podman exec comfyui true 2>/dev/null; do sleep 1; done'
ExecStart=podman exec --user oldmangan comfyui \
    /bin/bash -c 'cd /home/user/Development/ai_ecosystem/ComfyUI && ./venv/bin/python3 main.py --listen 0.0.0.0 --port 8188'
```

Install into `~/.config/systemd/user/`, then `systemctl --user daemon-reload && systemctl --user enable --now comfyui`.
Run `loginctl enable-linger` if you want it up before you log in. Note the **absolute paths** — they
must be updated if the project ever moves.

## 8. Models — the part you have to bring yourself

Model weights are third-party, licensed separately, and total ~54 GB here. They are not vendored and
never should be. Download into `models/checkpoints/`.

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

## VRAM budget

12 GB total, **shared with KoboldCPP**. Running a large LLM and an SDXL checkpoint at once will OOM.
Either run one heavy model at a time, or pair KoboldCPP with an SD 1.5 checkpoint. `stack off` frees
the GPU entirely for gaming.

## Topology

ComfyUI runs on **host networking**, not on `ai-net`:

- from the host → `localhost:8188`
- from containers on `ai-net` (companion, story, iguana) → `host.containers.internal:8188`

It's the one non-container backend in the stack, which is also why `stack` stops it by killing the
PID on its port rather than via `podman stop`.
