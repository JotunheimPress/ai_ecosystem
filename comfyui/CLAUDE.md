# ComfyUI

> **Notes and recipe live here; mutable state lives at `~/Development/ai_ecosystem/ComfyUI/`.**
> That directory is an upstream `comfyanonymous/ComfyUI` checkout, so anything of ours kept in it
> would be untracked and at risk from updates/reinstalls. Since the containerization, the stack uses
> that checkout **only for its state dirs** (`models`, `input`, `output`, `user`, `custom_nodes`) —
> the code that runs is cloned into the image at a pinned tag.

Node-graph image/video generation for the **AI companion (project #1)** — character portraits and
expression sprites. Runs as a **Podman container** on `ai-net`, GPU via CDI, listening on `:8188`.

## Structure (this folder = the canonical recipe)

- `Containerfile` — `ubuntu:24.04` + venv + cu130 torch + ComfyUI cloned at `ARG COMFYUI_VERSION`
- `start.sh` — builds the image if missing, then runs detached; mirrors `koboldcpp/start.sh`
- `.containerignore` — keeps docs/rollback lock out of the build context
- `requirements.lock.v0.31.1.txt` — the pinned environment, COPYed into the build
- `requirements.lock.pre-0.31.txt` — rollback artifact (see branch `pre-update-v0.18.1`); not a target
- `INSTALL.md` — full install/repro guide

## Running

```bash
stack comfyui          # preferred
stack logs comfyui     # podman logs -f comfyui
./start.sh --lowvram   # extra flags pass through to main.py
```

UI/API at http://localhost:8188. Cold start ~5 s.

## GPU

`--device nvidia.com/gpu=all` (CDI) **plus `--security-opt label=disable`** — without the latter,
SELinux blocks `/dev/nvidia*` and torch reports `No CUDA GPUs are available` after an otherwise
healthy-looking startup. Confirm with `curl -s localhost:8188/system_stats | jq '.devices[0]'`.

Only **12 GB VRAM** total, shared with KoboldCPP — running both large models at once will OOM.
Prefer one heavy model at a time, or pair KoboldCPP with an SD 1.5 checkpoint.

## Role in the companion

Image backend for the **custom companion app** at `~/Development/ai_ecosystem/companion/` (NOT
SillyTavern — that's legacy). The app's `services/comfyui.py` drives this server over the HTTP API at
**`http://comfyui:8188`** (by container name on `ai-net`; it was `host.containers.internal:8188`
before the containerization). It generates one base portrait (txt2img) then derives each emotion
sprite via img2img, so all expressions are the same character. See
`~/Development/ai_ecosystem/companion/CLAUDE.md` for the full expression pipeline.

## History

Ran in a `--nvidia` distrobox until Aug 2026. Nothing required distrobox; containerizing removed the
`kind=proc` special case in `stack`, the `distrobox enter` hang, the hand-rolled log rotation, and
the host-venv interpreter trap. The old container was renamed `comfyui-distrobox-old` rather than
deleted. See `INSTALL.md` → *Why not distrobox*.
