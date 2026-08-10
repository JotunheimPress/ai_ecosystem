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
- `ai-stack-backends.service` — systemd user unit template (copy into `~/.config/systemd/user/`).
- `koboldcpp/` — the **canonical** KoboldCPP recipe: `Containerfile`, `start.sh`, `chatml.json`,
  `.containerignore`, plus `INSTALL.md` / `CLAUDE.md`. The working tree at
  `~/Development/ai_ecosystem/koboldcpp/` symlinks back to these, so the running stack and the
  published recipe can't drift; only `models/` (~15 GB) stays out there, uncommitted.
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
stack companion    # start one app (companion|story|iguana|koboldcpp|comfyui)
stack down         # stop everything   (also: down backends | down frontends | down <app>)
stack restart <app>
stack logs <app>   # follow logs (podman logs -f — every app is a container)

stack install      # one-time setup, idempotent: build backend images, create ai-net,
                   #   install the systemd unit, link onto PATH.
                   #   targets: all|backends|koboldcpp|comfyui|service|path
stack doctor       # check prerequisites + what's installed; changes nothing
```

Typical: `stack up` (backends) → `stack` and pick a frontend, or `stack companion` directly.

## Registry (apps it manages)

| App        | Kind      | Port | Backend it needs | How it starts |
|------------|-----------|------|------------------|---------------|
| koboldcpp  | container | 5001 | —                | `~/Development/ai_ecosystem/koboldcpp/start.sh` (detached, on `ai-net`) |
| comfyui    | container | 8188 | —                | `ai-stack/comfyui/start.sh` (detached, on `ai-net`) |
| companion  | container | 7861 | kobold + comfyui | `~/Development/ai_ecosystem/companion/run-podman.sh` |
| story      | container | 7860 | kobold (+comfyui)| `~/Development/ai_ecosystem/ai_story_maker/run-podman.sh` |
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
copies the systemd unit and `daemon-reload`s → creates `ai-net` → builds the two backend images →
creates the ComfyUI state skeleton (`models/checkpoints`, `input`, `output`, `user`, `custom_nodes`).

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

```bash
# install the unit (once). COPY it — do NOT symlink (systemctl disable deletes a symlinked unit).
cp ~/Development/ai_ecosystem/ai-stack/ai-stack-backends.service ~/.config/systemd/user/
systemctl --user daemon-reload
loginctl enable-linger        # start at boot even before you log in
stack on                      # arm it (enable + start)

systemctl --user status ai-stack-backends   # low-level check
journalctl --user -u ai-stack-backends -f   # boot logs
```

Reuses the stack's own start logic (DRY), verified under the systemd `--user` context. Frontends stay manual — the switch only governs the shared backends.

## Notes / gotchas

- `koboldcpp/start.sh` now runs **detached** (`podman run -d --rm`) so the stack can start-and-forget it;
  it still loads the ChatML adapter (`chatml.json`). Model reload after a restart takes ~20–30 s.
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
  removes symlinked units (which then breaks `stack on`).
- The old `sillytavern-net` may linger while stopped legacy containers (mongodb, old ai-story-maker) are
  still associated with it; it clears once they're recreated on `ai-net`.
- This dir isn't a git repo yet — `git init` if you want it versioned.
