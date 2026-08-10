# ComfyUI

> **Notes live here, the install lives at `~/Development/ai_ecosystem/ComfyUI/`.** That directory is
> an upstream `comfyanonymous/ComfyUI` checkout (detached HEAD at a release tag), so anything of ours
> kept in it would be untracked and at risk from updates/reinstalls. `requirements.lock.pre-0.31.txt`
> in this folder is the pre-v0.31 rollback lock (see the rollback branch `pre-update-v0.18.1`).

Node-graph image/video generation for the **AI companion (project #1)** — character portraits
and expression sprites for SillyTavern cards. Runs **inside the `comfyui` distrobox**
(Ubuntu 24.04) using its own venv, with GPU passthrough to the RTX 4070.

## Structure

- `main.py` — server entrypoint
- `venv/` — Python 3.12 virtualenv (built inside the `comfyui` distrobox, not on the host)
- `models/` — model tree (`checkpoints`, `loras`, `controlnet`, `vae`, `animatediff`, `insightface`, …)
- `custom_nodes/` — extension nodes
- `input/`, `output/` — image I/O
- `comfyui.log` — server log

## Running

Launch inside the distrobox (this is where the venv's Python lives):

```bash
distrobox enter comfyui
cd ~/Development/ai_ecosystem/ComfyUI && ./venv/bin/python3 main.py --listen 0.0.0.0 --port 8188
```

UI/API at http://localhost:8188.

## GPU

The `comfyui` distrobox was created with `--nvidia`; confirm with `nvidia-smi` inside the box.
Only **12 GB VRAM** total — mind concurrent use with KoboldCPP (running both large models at
once will OOM). Prefer running one heavy model at a time.

## Role in the companion

Image backend for the **custom companion app** at `~/Development/ai_ecosystem/companion/` (NOT SillyTavern —
that's legacy). The app's `services/comfyui.py` drives this server over the HTTP API at
`host.containers.internal:8188`: it generates one base portrait (txt2img) then derives each
emotion sprite via img2img, so all expressions are the same character. See
`~/Development/ai_ecosystem/companion/CLAUDE.md` for the full expression pipeline.

Shares 12 GB VRAM with KoboldCPP — use SD 1.5 checkpoints (e.g. `absolutereality_v181`) when
both run concurrently.
