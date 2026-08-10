# ai-stack

One command to run a local, self-hosted AI stack. It manages the shared GPU **backends**
(LLM + image generation) and the **frontends** that use them — so you bring the backends up
once, then pick whichever app you want.

## Components

**Backends (shared)**
- **KoboldCPP** — local LLM text generation · `:5001`
- **ComfyUI** — image / video generation · `:8188` (runs in a distrobox)

**Frontends** (run side-by-side on their own ports)

| App | Port | What |
|-----|------|------|
| `companion` | 7861 | character-card AI companion |
| `story` | 7860 | AI Story Maker |
| `iguana` | 7862 | ComfyUI web GUI |

## Usage

```bash
stack on          # enable the AI backend — start now + at boot
stack off         # disable it — stop everything + no boot restart (frees the GPU to game)
stack             # interactive: show status, ensure backends, then pick a frontend
stack up          # start the backends now
stack status      # what's running (+ the on/off switch state)
stack companion   # launch a frontend  (companion | story | iguana)
stack down        # stop everything    (also: down backends | down frontends | down <app>)
stack restart <app>
stack logs <app>
```

## The on/off switch (game ⇄ AI)

`stack off` stops KoboldCPP + ComfyUI **and** disables the boot service — freeing all GPU VRAM
for gaming, with nothing restarting until you `stack on`. It's the single point of control.

## Boot auto-start

A systemd **user** service (`ai-stack-backends.service`) brings the backends up at login/boot so
they're ready and you just pick a frontend. Install/enable details in [`CLAUDE.md`](CLAUDE.md).

## How it works

- Containers share the **`ai-net`** Podman network and reach KoboldCPP by name (`koboldcpp:5001`);
  ComfyUI is reached at `host.containers.internal:8188`.
- The app list is a small **registry** at the top of the `stack` script — adding a new frontend is
  one row plus one start line.

## Requirements

Podman, distrobox, curl, and an NVIDIA GPU. Designed for an immutable Fedora/Bazzite workstation.

## Installing the backends

`stack` is a **launcher, not an installer** — it assumes the backends already exist. To build one
from scratch:

- **KoboldCPP** — [`koboldcpp/INSTALL.md`](koboldcpp/INSTALL.md). The full recipe is in this repo
  (`Containerfile`, `start.sh`, `chatml.json`); bring your own GGUF and run `./start.sh`.
- **ComfyUI** — [`comfyui/INSTALL.md`](comfyui/INSTALL.md): distrobox creation, the CUDA-pinned
  torch install, pinned requirements, systemd unit, and the model shopping list.

Model weights are never vendored — both guides list what's in use and where to get it.

Note that `stack` resolves projects under `$HOME/Development/ai_ecosystem` and expects them as
sibling directories, so it isn't turnkey on a different layout without editing that path.
