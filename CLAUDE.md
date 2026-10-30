# ai-stack — unified AI backend + frontend launcher

One `stack` command to run the **shared AI backends** and then **pick a frontend**. Backends are
shared across every app; frontends run side-by-side on their own ports.

## Structure

- `stack` — the launcher (bash). Registry of apps is a block of `reg` rows near the top; add a new
  frontend = one row. Symlinked onto PATH at `~/.local/bin/stack`.
  **Path resolution:** `ECO` is `$AI_ECOSYSTEM` if set, else derived from the script's own location
  (`readlink -f` resolves the PATH symlink; the ecosystem root is the checkout's parent). Projects
  are expected as siblings. Don't reintroduce a hardcoded path. `SELF` is the ai-stack checkout
  itself (resolved the same way), used to reach the in-repo `comfyui/start.sh` recipe.
- `ai-stack-backends.service` — systemd user unit **template** (`@SELF@` → the checkout path);
  rendered into `~/.config/systemd/user/` by `stack install service`.
- `koboldcpp/` — the **canonical** KoboldCPP recipe: `Containerfile`, `start.sh`, the chat adapters (`chatml.json`, `mistral-nemo.json`),
  `.containerignore`, plus `INSTALL.md` / `CLAUDE.md`. The working tree at
  `~/Development/ai_ecosystem/koboldcpp/` symlinks back to these, so the running stack and the
  published recipe can't drift; only `models/` (~15 GB) stays out there, uncommitted.
  `stack install koboldcpp` builds that symlink farm — it has to exist for the backend to run at
  all (`start.sh` resolves `models/` next to itself), so leaving it hand-made meant a fresh clone
  could never install. **One exception: `.containerignore` is a real copy, not a link** — podman
  does not follow a symlinked ignore file (it behaves as if there were none), and the build would
  then tar the whole `models/` tree. Install refreshes that copy so it can't drift either.
- `stack-ui/` — **AI Ecosystem**, the control dashboard (a pytincture + wapyt app — Python in
  the browser — with its own Containerfile and `CLAUDE.md`). Lives in this repo rather than as a sibling project because it *is*
  part of the stack rather than a consumer of it, so `stack install` can build it and `start_app`
  reaches it via `$SELF` like the comfyui recipe. Was briefly a standalone repo
  (`github.com/El-Iguana/stack-ui`), imported here with its history via `git subtree`.
- `lib/host.sh` + optional `host.env` (git-ignored; `host.env.example`) — the **host profile**:
  `GPU=nvidia|amd`, `LLM_BACKEND=cuda|vulkan|rocm`, `BIND`. Sourced by `stack` and every backend
  script; it supplies the GPU flags, image tags and published-port address. Without `host.env`
  the GPU is detected — NVIDIA CDI first, since this workstation also has `/dev/kfd` from its
  CPU's graphics — and the 4070 setup is byte-for-byte what it was. The AMD Halo box is the
  other profile (`HALO_PLAN.md`); both stay supported.
- `router/` — the **backend router**: one nginx container (`backend-router`, stock
  `nginx:1.28-alpine` + `router.conf.template`) that owns the names `koboldcpp` and `orpheus-tts` on
  ai-net and host ports **5001** (all interfaces) and **127.0.0.1:5005**, and forwards each to where
  that backend runs. The local copies run as `koboldcpp-local` / `orpheus-tts-local` with no host
  ports. Route state is `$ECO/router/route.conf`, written only by `stack route`. Groundwork for the
  Halo box (`HALO_PLAN.md`). See `router/CLAUDE.md`.
- `lan-proxy/` — the **LAN proxy**, off by default: one Caddy container (`lan-proxy`, stock
  `caddy:2.11.4`) that serves the apps to other devices over HTTPS on **8760–8764** (+ a plain-HTTP
  start page and CA download on **8780**), with its own local CA and a password in front of the apps
  without a login. `stack lan on|off` writes `lan.env` (git-ignored); `stack` exports
  `PANTHEON_PUBLIC_ORIGIN` + `IGUANA_ALLOWED_HOSTS` from it on every run. State in `$ECO/lan-proxy/`.
  See `lan-proxy/CLAUDE.md`.
- `orpheus-tts/` — Orpheus TTS for Pantheon's voice: a `start.sh` that runs the **koboldcpp image**
  in admin mode with no model, so it costs nothing until Pantheon loads it. State in
  `$ECO/orpheus-tts/` (models/, config/). See `orpheus-tts/CLAUDE.md`.
- `comfyui/` — the **canonical** ComfyUI recipe, same shape as `koboldcpp/`: `Containerfile`
  (ubuntu:24.04 + cu130 torch + ComfyUI cloned at a pinned tag), `start.sh`, `.containerignore`,
  `INSTALL.md`, `CLAUDE.md`, and two lock files — `requirements.lock.v0.31.1.txt` (current, COPYed
  into the build) and `requirements.lock.pre-0.31.txt` (rollback artifact). They live here rather
  than in `~/Development/ai_ecosystem/ComfyUI/`, which is an upstream checkout where our files would
  be untracked and lost on update. That checkout now supplies **only mutable state** (`models`,
  `input`, `output`, `user`, `custom_nodes`), bind-mounted at `/data` via `--base-directory`.

## Usage

```bash
stack on / stack off  # SINGLE SWITCH for the AI backend (the one to remember):
                      #   off = stop backends + won't restart at boot  → free the GPU to game
                      #   on  = start backends now + re-arm boot auto-start
stack              # interactive: show status, offer to start backends, then pick a frontend
stack up           # start backends now WITHOUT changing the boot toggle
stack status       # what's up (+ shows the ON/OFF switch state)
stack companion    # start one app (companion|story|iguana|koboldcpp|comfyui|orpheus-tts)
stack down         # stop everything   (also: down backends | down frontends | down <app>)
stack restart <app>
stack logs <app>   # follow logs (podman logs -f — every app is a container)
stack route [local|halo]  # show / set where text + voice are served (halo needs HALO_HOST in halo.env)

stack install      # one-time setup, idempotent: build backend images, create ai-net,
                   #   install the systemd unit, link onto PATH.
                   #   targets: all|backends|koboldcpp|comfyui|service|path
stack doctor       # check prerequisites + what's installed; changes nothing
```

Typical: `stack up` (backends) → `stack` and pick a frontend, or `stack companion` directly.

## Registry (apps it manages)

| App        | Kind      | Port | Backend it needs | How it starts |
|------------|-----------|------|------------------|---------------|
| router     | container | 5001, 5005 (127.0.0.1) | — | `ai-stack/router/start.sh` — container `backend-router`; started first, since the two below are health-probed through it |
| koboldcpp  | container | (via router) | —        | `~/Development/ai_ecosystem/koboldcpp/start.sh` (detached, on `ai-net`, container `koboldcpp-local`) |
| comfyui    | container | 8188 | —                | `ai-stack/comfyui/start.sh` (detached, on `ai-net`) |
| orpheus-tts | container | (via router) | — | `ai-stack/orpheus-tts/start.sh` (container `orpheus-tts-local`) — Pantheon's expressive voice; idle with **no model / no VRAM** until voice is on (`orpheus-tts/CLAUDE.md`) |
| companion  | container | 7861 (127.0.0.1 only) | kobold + comfyui | `~/Development/ai_ecosystem/companion/run-podman.sh` — **Pantheon**; health `/api/health` |
| story      | container | 7860 | kobold (+comfyui)| `~/Development/ai_ecosystem/ai_story_maker/run-podman.sh` — **Rhapsode** |
| iguana     | container | 7862 | comfyui          | `~/Development/ai_ecosystem/iguana_studio/comfyui-auto/run-podman.sh` (on `ai-net`; reaches ComfyUI by name at `comfyui:8188`) |

## Topology

- **Shared network `ai-net`** (renamed from the legacy `sillytavern-net`). Containerized apps join it
  and reach KoboldCPP by name at `http://koboldcpp:5001`.
- **ComfyUI** is a container on `ai-net` publishing `8188` → `localhost:8188` from the host,
  `http://comfyui:8188` by name from other containers on the network.
- Frontends don't conflict — distinct ports (7861/7860/7862), so several can run at once. "Pick a
  frontend" is about convenience, not exclusivity.

## Adding a frontend

Add one `reg <name> <port> <health-url> "<desc>" <container|proc> [container-name]` row, add it to the
`FRONTENDS=(…)` list, and add its start line to `start_app()` (a `run-podman.sh` for containers, or a
backgrounded process for a host-side `proc` app). Stop logic keys off `KIND`.

## Install (`stack install`)

Executable form of `koboldcpp/INSTALL.md` + `comfyui/INSTALL.md`, so the docs and the real thing
can't drift. Every step is idempotent — re-running reports what's already in place and does nothing.

What it does: checks prerequisites (podman, host driver, CDI spec) → links `~/.local/bin/stack` →
renders the systemd unit and `daemon-reload`s → creates `ai-net` → links the KoboldCPP recipe into
`$ECO/koboldcpp` → builds the two backend images →
creates the ComfyUI state skeleton (`models/checkpoints`, `input`, `output`, `user`, `custom_nodes`)
→ clones the ComfyUI **custom nodes** at their pinned commits.

Custom nodes come from the `CUSTOM_NODES=(…)` registry near the top of `stack` — one
`name|repo|pinned-commit` row each, mirroring the app registry. The clone is idempotent and
self-healing: on-pin is a no-op, off-pin is fetched and re-checked-out, and a directory that isn't a
git checkout is reported and left untouched rather than clobbered. Clones are deliberately **not**
shallow — `--depth 1` only carries the tip, so an older pin would stop resolving once upstream moved.
`stack doctor` reports the same three states read-only.

**A node's Python deps do not go in that registry.** `custom_nodes/` is bind-mounted state, but the
container is recreated on every start, so a runtime `pip install` is gone by the next one — deps
belong in `comfyui/Containerfile`. Adding a row here usually means adding a line there too.

**It never downloads model weights** — they're third-party and ~73 GB. It reports what's missing and
points at the relevant INSTALL.md section instead. Because of that, a fresh install ends with
*"install done, N item(s) need you"* rather than "complete": the images are built but nothing can
start until you supply a GGUF and a checkpoint.

`stack doctor` is the read-only half — prerequisites, images, network, unit, plus `stack status`.

## The on/off switch (gaming ↔ AI) + boot auto-start

`ai-stack-backends.service` — a **systemd user** unit (oneshot, `stack up`/`stack down backends`) is the
**single source of truth**. `stack on`/`stack off` drive it:
- **`stack off`** → `systemctl --user disable --now` → stops KoboldCPP + ComfyUI **and** removes the boot
  start. Nothing brings them back until you `stack on`, so the GPU stays free for gaming across reboots.
- **`stack on`** → `systemctl --user enable --now` → starts them now and re-arms boot.

The shipped unit is a **template**: `@SELF@` is substituted with the checkout's own path at
install time, so it works wherever ai-stack is cloned. Install it with `stack install service`, not
`cp` — a raw copy has an unsubstituted placeholder and systemd rejects it (loudly, which is the
point; the old hardcoded `~/Development/ai_ecosystem/...` path failed *silently* on any other layout).

```bash
stack install service         # render the unit + daemon-reload (COPY, never a symlink —
                              #   systemctl disable deletes a symlinked unit, breaking `stack on`)
loginctl enable-linger        # start at boot even before you log in
stack on                      # arm it (enable + start)

systemctl --user status ai-stack-backends   # low-level check
journalctl --user -u ai-stack-backends -f   # boot logs
```

Reuses the stack's own start logic (DRY), verified under the systemd `--user` context. Frontends stay manual — the switch only governs the shared backends.

## Notes / gotchas

- `koboldcpp/start.sh` runs the container **detached** (`podman run -d`) but returns only once the
  model has loaded (or failed): it waits for the old container's VRAM, asks ComfyUI to unload its
  cache and Orpheus TTS, retries in CUDA unified memory when VRAM is merely fragmented, and on a
  real shortage retries with fewer layers — see `koboldcpp/CLAUDE.md`. It picks the chat format
  (adapter) from the model file: ChatML for Qwen, `mistral-nemo.json` for Rocinante, AutoGuess
  otherwise. A swap takes ~25 s; a fallback ~35–50 s.
- **Both backends run with `--restart=on-failure:5`, not `--rm`.** Before this, a crashed backend
  vanished without a trace while the switch still said ON (found 2026-09-24: both gone, no container,
  no logs). Podman now restarts a crash up to 5 times; `podman stop` (`stack down`/`stack off`) is a
  deliberate stop and is never restarted — unlike the old `comfyui.service` below. Stopped containers
  linger until the next `start.sh` removes them, so `stack logs` works after a crash.
- **Every app is a container now.** ComfyUI was the last `kind=proc` holdout (a distrobox process); it
  was containerized in Aug 2026, so `podman stop` / `podman logs` work uniformly across the registry.
  The `proc` branch in `stop_app`/`start_one` and the `port_pid()` helper are kept as the extension
  point for a future host-side app, but nothing uses them today.
- **ai-stack is the sole owner of ComfyUI.** A standalone `~/.config/systemd/user/comfyui.service`
  (`podman exec` into the old distrobox, `Restart=on-failure`, `WantedBy=default.target`) used to
  compete with it — the unit file still exists but is **disabled**; don't re-enable it. Symptoms it
  caused, for future reference: its `ExecStartPre` (`until podman exec comfyui true`) parks forever
  while the container is stopped, then fires the instant *anything* starts the container — including
  `stack up` — so it won the race to :8188 and the stack's own copy died with "Port 8188 is already in
  use" while the health probe still reported `✓` (it was probing the *other* instance). It also undid
  `stack down comfyui` five seconds later via `Restart=on-failure`; its restart counter had reached 65.
- **ComfyUI needs `--security-opt label=disable`**, not just `--device nvidia.com/gpu=all`. SELinux
  otherwise blocks the CDI-injected `/dev/nvidia*` nodes and torch reports `No CUDA GPUs are
  available` after an otherwise healthy-looking startup. It's narrower than koboldcpp's `--privileged`.
  Corollary: **no `:z` on its volumes** — relabeling is redundant with labels disabled, and `:z` would
  walk the whole 58 GB `models/` tree on every start.
- `start_one` refuses to start a `proc` app whose port is already held but not answering health, and
  names the holding PID instead of launching a doomed second copy.
- The frontends reach ComfyUI at **`http://comfyui:8188`** by name on `ai-net`, matching
  `http://koboldcpp:5001`. `host.containers.internal:8188` still works while the port is published,
  but it's no longer the default anywhere.
- The systemd unit must be a **copy** in `~/.config/systemd/user/`, not a symlink — `systemctl disable`
  removes symlinked units (which then breaks `stack on`). `install_service` renders it from the
  `@SELF@` template; `stack doctor` warns if the installed unit's `ExecStart` points at a *different*
  checkout, which otherwise enables cleanly and only fails at boot.
- **`.containerignore` can never be a symlink.** Podman silently doesn't follow one — verified
  against podman 5.8: a symlinked ignore file behaves exactly like no ignore file. The build still
  succeeds, it just tars ~15 GB of GGUFs into the context first. Applies to `comfyui/` too if that
  recipe is ever mirrored out the way `koboldcpp/` is.
- The old `sillytavern-net` may linger while stopped legacy containers (mongodb, old ai-story-maker) are
  still associated with it; it clears once they're recreated on `ai-net`.
- This dir is a git repo, pushed to `github.com/JotunheimPress/ai-stack`. Its default branch is
  **`master`**, while the sibling `iguana_studio` repo uses `main` — worth knowing before scripting
  across both.
