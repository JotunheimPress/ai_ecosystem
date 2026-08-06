# ai-stack — unified AI backend + frontend launcher

One `stack` command to run the **shared AI backends** and then **pick a frontend**. Backends are
shared across every app; frontends run side-by-side on their own ports.

## Structure

- `stack` — the launcher (bash). Registry of apps is a block of `reg` rows near the top; add a new
  frontend = one row. Symlinked onto PATH at `~/.local/bin/stack`.

## Usage

```bash
stack              # interactive: show status, offer to start backends, then pick a frontend
stack up           # start the backends (koboldcpp + comfyui) and wait for them
stack status       # what's up (backends + frontends, with ports)
stack companion    # start one app (companion|story|iguana|koboldcpp|comfyui)
stack down         # stop everything   (also: down backends | down frontends | down <app>)
stack restart <app>
stack logs <app>   # follow logs (podman logs for containers; log file for comfyui/iguana)
```

Typical: `stack up` (backends) → `stack` and pick a frontend, or `stack companion` directly.

## Registry (apps it manages)

| App        | Kind      | Port | Backend it needs | How it starts |
|------------|-----------|------|------------------|---------------|
| koboldcpp  | container | 5001 | —                | `~/koboldcpp/start.sh` (detached, on `ai-net`) |
| comfyui    | proc      | 8188 | —                | `comfyui` distrobox venv (`python main.py --listen`) |
| companion  | container | 7861 | kobold + comfyui | `~/Development/companion/run-podman.sh` |
| story      | container | 7860 | kobold (+comfyui)| `~/ai_story_maker/run-podman.sh` |
| iguana     | proc      | 7862 | comfyui          | `~/workspace/iguana_studio/comfyui-auto` venv (`PORT=7862 python server.py`) |

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

## Boot auto-start (backends only)

`ai-stack-backends.service` — a **systemd user** unit (oneshot) that runs `stack up` at boot, so
KoboldCPP + ComfyUI are ready and you just pick a frontend. Frontends stay manual.

```bash
# install (once): symlink the repo unit, enable, and allow boot-without-login
ln -sf ~/ai-stack/ai-stack-backends.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ai-stack-backends.service
loginctl enable-linger              # start at boot even before you log in

systemctl --user status ai-stack-backends     # check
journalctl --user -u ai-stack-backends -f     # logs
systemctl --user disable --now ai-stack-backends.service   # opt out
```

Reuses the stack's own start logic (so it stays DRY), including the distrobox ComfyUI start (verified
to work under the systemd `--user` context). `ExecStop` runs `stack down backends`.

## Notes / gotchas

- `koboldcpp/start.sh` now runs **detached** (`podman run -d --rm`) so the stack can start-and-forget it;
  it still loads the ChatML adapter (`chatml.json`). Model reload after a restart takes ~20–30 s.
- `comfyui`/`iguana` are host/distrobox processes (not containers) — stopped via `pkill` patterns, logs
  are tailed from their log files, not `podman logs`.
- The old `sillytavern-net` may linger while stopped legacy containers (mongodb, old ai-story-maker) are
  still associated with it; it clears once they're recreated on `ai-net`.
- This dir isn't a git repo yet — `git init` if you want it versioned.
