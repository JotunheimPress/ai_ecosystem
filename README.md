# ai-stack

One command to run a local, self-hosted AI stack. It manages the shared GPU **backends**
(LLM + image generation) and the **frontends** that use them — so you bring the backends up
once, then pick whichever app you want.

## Components

**Backends (shared)** — both are Podman containers on the `ai-net` network, GPU via CDI.

| Backend | Port | What |
|---|---|---|
| **KoboldCPP** | 5001 | local LLM text generation |
| **ComfyUI** | 8188 | image / video generation |

**Frontends** (run side-by-side on their own ports, each its own repo)

| App | Port | Directory | Repo | What |
|-----|------|-----------|------|------|
| `companion` | 7861 | `companion/` | `JotunheimPress/companion` | **Pantheon** — character-card AI companion |
| `story` | 7860 | `ai_story_maker/` | `JotunheimPress/ai-story-maker` | **Rhapsode** — the Narrative Engine |
| `iguana` | 7862 | `iguana_studio/` | `JotunheimPress/iguana_studio` | ComfyUI web GUI |

You only need the frontends you actually use; `stack` starts each on demand. The backends are
useful on their own (KoboldCPP and ComfyUI both ship their own web UI).

**Control**

| App | Port | Directory | What |
|-----|------|-----------|------|
| `stackui` | 7863 | `stack-ui/` (in this repo) | **AI Ecosystem** — dashboard for the stack itself |

A container like the rest, but one that controls the host: it mounts the podman socket and the
user session bus so it can drive them, and runs with `--network=host` so the health probes work.
It's bound to loopback and has no auth, so it is reachable only from this machine. `stack down all`
deliberately leaves it running; stop it with `stack down stackui`.

## Requirements

- **Podman** and an **NVIDIA GPU** with the container toolkit — `nvidia-smi` must work on the host
  and `nvidia-ctk cdi list` must show `nvidia.com/gpu=all`. `stack doctor` checks both.
- **git** (used to fetch pinned ComfyUI custom nodes) and **curl** (health checks).
- Optional: `jq`, for the GPU-verification commands in the install guides.

Designed for an immutable Fedora/Bazzite workstation — nothing here needs `dnf install`, and
distrobox is no longer used.

## Install

```bash
git clone https://github.com/JotunheimPress/ai-stack.git
cd ai-stack
./stack install          # idempotent — safe to re-run any time
```

`stack install` is the executable form of the two INSTALL guides, so they can't drift from the real
thing. It checks prerequisites, links `stack` onto your PATH, renders the systemd unit for *this*
checkout, creates the `ai-net` network, builds both backend images plus the AI Ecosystem
dashboard, lays out `koboldcpp/` and `ComfyUI/` next to the checkout, and clones the pinned
ComfyUI custom nodes.

Targets: `all` (default) · `backends` · `koboldcpp` · `comfyui` · `stackui` · `service` · `path`.

**It never downloads model weights** — they're third-party and ~73 GB. A fresh install therefore
ends with *"install done, N item(s) need you"*: the images are built, but nothing can start until
you supply at least

- a **GGUF** in `koboldcpp/models/` — see [`koboldcpp/INSTALL.md`](koboldcpp/INSTALL.md) §2
- a **checkpoint** in `ComfyUI/models/checkpoints/` — see [`comfyui/INSTALL.md`](comfyui/INSTALL.md) §8
  (start with `absolutereality_v181`, 2 GB)

Install reports exactly what's missing and where it goes. Then:

```bash
stack doctor             # read-only: prerequisites + what's installed, changes nothing
stack on                 # start the backends and arm boot auto-start
```

The two INSTALL guides cover the rest — reference hardware, version pins, the flags that matter,
how to verify the GPU is actually visible, and the model shopping list.

### Frontends

Clone the ones you want as **siblings** of this checkout. Directory names differ from the repo
names, so pass the target explicitly:

```bash
cd ..                                                              # the ecosystem root
git clone https://github.com/JotunheimPress/companion.git       companion
git clone https://github.com/JotunheimPress/ai-story-maker.git  ai_story_maker
git clone https://github.com/JotunheimPress/iguana_studio.git   iguana_studio
```

Each builds its own image on first `stack <app>`; see that repo's own README/`CLAUDE.md` for its
configuration. They're separate private repos — ask for access.

### AI Ecosystem (the control dashboard)

![AI Ecosystem dashboard: the two backends with KoboldCPP's loaded model and ComfyUI's VRAM, GPU use, and the frontends below](stack-ui/docs/dashboard.png)

The home screen shows the shared backends and what each has loaded, then the frontends; the
sidebar has the model managers (installed GGUFs + HuggingFace search, ComfyUI files + Civitai),
the VRAM planner, logs, custom-node pins and the doctor. Built with pytincture + wapyt.

Ships in this repo (`stack-ui/`) and is built by `stack install`, so there's nothing extra to clone:

```bash
stack install stackui                        # or just `stack install`
systemctl --user enable --now podman.socket  # how it reaches the host engine
stack stackui                                # serves http://localhost:7863
```

`stack install` warns if that socket isn't active — without it the dashboard builds fine and then
can't do anything.

## Usage

```bash
stack on          # enable the AI backend — start now + at boot
stack off         # disable it — stop everything + no boot restart (frees the GPU to game)
stack             # interactive: show status, ensure backends, then pick a frontend
stack up          # start the backends now (without touching the boot toggle)
stack status      # what's running (+ the on/off switch state)
stack companion   # launch a frontend  (companion | story | iguana)
stack stackui     # launch AI Ecosystem, the dashboard, on :7863
stack down        # stop everything    (also: down backends | down frontends | down <app>)
                  #   — leaves stackui alone; stop that with `down stackui`
stack restart <app>
stack logs <app>
stack install [target]   # one-time setup, idempotent
stack doctor             # check prerequisites + what's installed
```

## Other devices on the LAN

Every app answers on the whole local network over plain HTTP, no extra setup:
`http://<hostname>.local:7861` (Pantheon), `:7860` (Rhapsode), `:7862` (Iguana Studio), `:7864`
(Nocturne). Pantheon asks for its own accounts; the others have no login. It is a local product
for whoever installs it. Browsers allow the microphone only on this computer or over HTTPS, so
Pantheon's voice input works here but not from a phone. The dashboard is on the LAN too, at
`:7863`.

## The on/off switch (game ⇄ AI)

`stack off` stops KoboldCPP + ComfyUI **and** disables the boot service — freeing all GPU VRAM
for gaming, with nothing restarting until you `stack on`. It's the single point of control.

Both backends share **12 GB of VRAM** on the reference machine: a ~9B LLM at Q4 plus an SD 1.5
checkpoint fits, an LLM plus SDXL does not. ComfyUI also holds VRAM after a job — see the
`/free` call in [`comfyui/CLAUDE.md`](comfyui/CLAUDE.md).

## Boot auto-start

A systemd **user** service (`ai-stack-backends.service`) brings the backends up at login/boot so
they're ready and you just pick a frontend. `stack install service` renders and installs it —
the shipped file is a template whose `@SELF@` placeholder becomes this checkout's path, so it
works wherever you cloned. Don't `cp` it by hand.

```bash
loginctl enable-linger   # optional: start at boot even before you log in
stack on                 # arm it
```

## How it works

- Containers share the **`ai-net`** Podman network and reach the backends by name —
  `http://koboldcpp:5001` and `http://comfyui:8188`. Both are also published to the host
  (`localhost:5001` / `localhost:8188`).
- The app list is a small **registry** at the top of the `stack` script — adding a new frontend is
  one row plus one start line.
- Backend recipes live in this repo (`koboldcpp/`, `comfyui/`) and are the canonical copies;
  `stack install` wires them into the sibling directories where they run, so what you run and what
  you publish can't diverge.

## Layout

`stack` expects the projects as **sibling directories** next to this checkout:

```
ai_ecosystem/
├── ai-stack/       # this repo
├── koboldcpp/      # backend — created by `stack install`; you add models/
├── ComfyUI/        # backend state — created by `stack install`; you add models/
├── companion/      # frontend — you clone
├── ai_story_maker/ # frontend — you clone
└── iguana_studio/  # frontend — you clone
```

The two backend directories are made for you: `koboldcpp/` is a symlink farm back into this repo
plus a real `models/`, and `ComfyUI/` holds only mutable state (`models`, `input`, `output`, `user`,
`custom_nodes`) — the ComfyUI code itself ships inside the image, so there's no upstream checkout to
clone.

The root is found automatically from the script's own location, so cloning side by side is all
that's needed — nothing to configure. To keep the projects elsewhere, set `AI_ECOSYSTEM`:

```bash
export AI_ECOSYSTEM=/path/to/your/projects
```
