# Getting started

[Wiki home](README.md) · User guide

## What you need

- **Podman** (rootless) and a GPU the stack knows:
  - **NVIDIA** with the container toolkit: `nvidia-smi` works on the host and
    `nvidia-ctk cdi list` shows `nvidia.com/gpu=all`.
  - **AMD** (the Halo box, Strix Halo / gfx1151): `/dev/kfd` and `/dev/dri`, and your user in the
    `render` and `video` groups. See [`HALO_PLAN.md`](../../HALO_PLAN.md).
- **git** (pinned ComfyUI custom nodes) and **curl** (health checks).
- Disk for models: tens of GB.

The GPU is detected automatically. To pin it, copy `host.env.example` to `host.env` and set
`GPU=nvidia|amd`, and optionally `LLM_BACKEND`, `BIND` and `COMFYUI_PORT` (see
[Architecture](architecture.md#host-profiles)).

## Install

```bash
mkdir ai_ecosystem && cd ai_ecosystem   # the stack puts its other folders next to the checkout
git clone https://github.com/JotunheimPress/ai_ecosystem.git ai-stack
cd ai-stack
./stack install        # idempotent: safe to re-run any time
```

Clone it inside a folder of its own, as above: `stack install` creates `koboldcpp/`,
`ComfyUI/` and `fortuna/` *next to* the checkout (see [Directory layout](#directory-layout)),
and frontends are cloned there too.

`stack install` checks prerequisites, puts `stack` on your PATH (`~/.local/bin/stack`), installs the
systemd user unit for this checkout, creates the `ai-net` network, builds the backend images and
the dashboard, lays out `koboldcpp/` and `ComfyUI/` next to the checkout, and clones the pinned
ComfyUI custom nodes. You can install parts:
`stack install all|backends|router|koboldcpp|comfyui|fortuna|stackui|service|path`.

**It never downloads model weights.** A fresh install ends with *"install done, N item(s) need
you"* followed by the list of what is missing:

| Backend | What it needs | Where | How |
|---|---|---|---|
| KoboldCPP (text) | a `.gguf` | `koboldcpp/models/` | the dashboard's **Language models → Find on HuggingFace**, or by hand ([`koboldcpp/INSTALL.md`](../../koboldcpp/INSTALL.md) §2) |
| ComfyUI (image) | a checkpoint | `ComfyUI/models/checkpoints/` | **Image and audio → Find on Civitai / HuggingFace**, or by hand ([`comfyui/INSTALL.md`](../../comfyui/INSTALL.md) §8) |
| Fortuna (decisions) | one of its pinned files | `fortuna/models/` | **Language models → Fortuna → Download**, or `stack fortuna-model fetch NAME` |

See [Models](models.md).

## First start

```bash
stack doctor        # read-only: prerequisites, images, network, models
stack on            # start the backends now
stack boot on       # optional: also start them at every boot
stack stackui       # the dashboard, on http://localhost:7863
```

Two settings belong to your login session, so `stack install` checks them and warns rather than
changing them:

- `systemctl --user enable --now podman.socket`: the dashboard drives podman through it, and
  without it it opens but can't start or stop anything.
- `loginctl enable-linger`: lets the backends start at boot before you log in.

## Directory layout

The projects are **siblings** of the checkout (override the root with `AI_ECOSYSTEM`):

```
ai_ecosystem/
├── ai-stack/        # this repo
├── koboldcpp/       # made by stack install: links into ai-stack/koboldcpp + your models/
├── ComfyUI/         # made by stack install: models, input, output, user, custom_nodes
├── fortuna/         # Fortuna's models/ and fortuna.env
├── router/          # route.conf (where text goes)
└── <frontends>/     # each frontend you clone
```

Next: [Running the stack](running-the-stack.md).
