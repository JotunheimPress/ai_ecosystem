# ai-stack — unified AI backend + frontend launcher

One `stack` command to run the **shared AI backends** and then **pick a frontend**. Backends are
shared across every app; frontends run side-by-side on their own ports.

## Structure

- `stack` — the launcher (bash). Registry of apps is a block of `reg` rows near the top; add a new
  frontend = one row. Symlinked onto PATH at `~/.local/bin/stack`.
- `ai-stack-backends.service` — systemd user unit template (copy into `~/.config/systemd/user/`).
- `comfyui/` — notes + pinned requirements for the ComfyUI backend. They live here rather than in
  `~/Development/ai_ecosystem/ComfyUI/`, which is an upstream checkout where our files would be
  untracked and lost on update.

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
stack logs <app>   # follow logs (podman logs for containers; log file for comfyui/iguana)
```

Typical: `stack up` (backends) → `stack` and pick a frontend, or `stack companion` directly.

## Registry (apps it manages)

| App        | Kind      | Port | Backend it needs | How it starts |
|------------|-----------|------|------------------|---------------|
| koboldcpp  | container | 5001 | —                | `~/Development/ai_ecosystem/koboldcpp/start.sh` (detached, on `ai-net`) |
| comfyui    | proc      | 8188 | —                | `comfyui` distrobox venv (`python main.py --listen`) |
| companion  | container | 7861 | kobold + comfyui | `~/Development/ai_ecosystem/companion/run-podman.sh` |
| story      | container | 7860 | kobold (+comfyui)| `~/Development/ai_ecosystem/ai_story_maker/run-podman.sh` |
| iguana     | container | 7862 | comfyui          | `~/Development/ai_ecosystem/iguana_studio/comfyui-auto/run-podman.sh` (on `ai-net`; reaches ComfyUI via `host.containers.internal:8188`) |

## Topology

- **Shared network `ai-net`** (renamed from the legacy `sillytavern-net`). Containerized apps join it
  and reach KoboldCPP by name at `http://koboldcpp:5001`.
- **ComfyUI** runs in the `comfyui` distrobox on host networking → reachable at `localhost:8188` (host)
  / `host.containers.internal:8188` (from containers). Non-container frontends use `localhost:8188`.
- Frontends don't conflict — distinct ports (7861/7860/7862), so several can run at once. "Pick a
  frontend" is about convenience, not exclusivity.

## Adding a frontend

Add one `reg <name> <port> <health-url> "<desc>" <container|proc> [container-name]` row, add it to the
`FRONTENDS=(…)` list, and add its start line to `start_app()` (a `run-podman.sh` for containers, or a
backgrounded process for host/distrobox apps). Stop logic keys off `KIND`.

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

Reuses the stack's own start logic (DRY), including the distrobox ComfyUI start (verified under the
systemd `--user` context). Frontends stay manual — the switch only governs the shared backends.

## Notes / gotchas

- `koboldcpp/start.sh` now runs **detached** (`podman run -d --rm`) so the stack can start-and-forget it;
  it still loads the ChatML adapter (`chatml.json`). Model reload after a restart takes ~20–30 s.
- **`comfyui` is the only non-container backend** — a distrobox process. It's visible in the host PID
  namespace (listens on a host port), so the stack stops it by **killing the PID on its port**
  (`ss -tlnHp`) — robust, unlike `pkill -f` which self-matches the `distrobox enter` wrapper. Its logs
  are tailed from `~/Development/ai_ecosystem/ComfyUI/comfyui.log`, not `podman logs`. (companion/story/iguana are all containers.)
- The systemd unit must be a **copy** in `~/.config/systemd/user/`, not a symlink — `systemctl disable`
  removes symlinked units (which then breaks `stack on`).
- The old `sillytavern-net` may linger while stopped legacy containers (mongodb, old ai-story-maker) are
  still associated with it; it clears once they're recreated on `ai-net`.
- This dir isn't a git repo yet — `git init` if you want it versioned.
