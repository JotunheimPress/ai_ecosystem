# The `stack` script

[Wiki home](README.md) · Developer guide

`stack` is one bash script at the repo root, linked onto PATH as `~/.local/bin/stack`. It owns
every operation; the dashboard only ever runs it. Full notes: [`CLAUDE.md`](../../CLAUDE.md).

## The registry

Near the top, one `reg` row per built-in app (name, port, health URL, description, kind,
container), plus tables keyed by app: `LABEL` (facing names), `CAP` (capability: routing, text,
image-sound, decision) and `LOCAL_PROBE` (how to check the local copy). **Frontends are not in
the registry:** they are discovered from `frontend.json` files under the ecosystem root (parsed
with python3) on every run. `FRONTEND_RESERVED` lists the names a frontend may not take because
`stack <name>` already means something.

ComfyUI custom nodes are a second registry, `CUSTOM_NODES=(name|repo|commit …)`. A node's Python
dependencies go in `comfyui/Containerfile`, not there.

## `stack status --json`

What the dashboard reads; only JSON goes to stdout. The shape is documented above `status_json`.

- `boot`: `on`, `off`, or null with no unit (also emitted as `switch`, its old name).
- `route`: where text goes and whether the running router's config is current.
- per app: `group` (backend/frontend/control), `label`, `capability`, `port`, `up`, and for
  backends `local`, the state of **this machine's copy** (`up`, `loading`, `stopped`, `absent`,
  `down`).
- `up` is the *served* state, through the router. `local` is probed only when served can't stand
  for it (routed elsewhere, or the router is down).
- Health probes run in parallel, so status costs one timeout however many apps are down.

## Install and doctor

`stack install [target]` is the executable form of the INSTALL guides; every step is idempotent
and reports what's already in place. It builds images, renders the unit, creates `ai-net`, links
the KoboldCPP recipe, lays out ComfyUI's state and clones the pinned nodes. **It never downloads
weights.** `stack doctor` is its read-only half, plus `stack status`. Lines start with `✓` or `!`,
which the dashboard parses.

## Running now versus at boot

- `stack on` / `stack up` start the backends (router first); `stack off` / `stack down backends`
  stop them (router last). None of them touch the unit.
- `stack boot on|off` is a plain `systemctl --user enable|disable` of the unit, never `--now`.
- The unit must be a **copy** in `~/.config/systemd/user/`, never a symlink: `systemctl disable`
  deletes a symlinked unit.

## Other commands

`stack route [local|halo]` (writes `route.conf`, reloads the router, waits for the new route),
`stack frontends [--json]`, `stack fortuna-model [list|fetch|verify]`, `stack logs`, `stack restart`,
`stack gpu-release` / `stack gpu-restore` (unload KoboldCPP's model to free its GPU memory, and load
it again, inside its running container: `koboldcpp/CLAUDE.md` → *Release and restore*).
`stack --help` prints the header comment, which is kept as the command reference.
