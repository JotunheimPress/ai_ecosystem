# stack-ui — control dashboard for the AI stack

Web frontend for the stack itself. **Phase 0 (built): read-only dashboard** — service
status, model inventory, disk, and `stack doctor` on demand. Nothing here can change the
state of the machine yet; that's phase 1.

Full roadmap: [`../ai-stack/STACK_UI_PLAN.md`](../ai-stack/STACK_UI_PLAN.md).

## Why it runs on the host

`stack` drives **podman *and* `systemctl --user`**. Containerizing this would break the
on/off switch — the single most useful button. So it's the first `kind=proc` app in the
registry, the slot `stack` has been holding open since ComfyUI was containerized.

Consequence: it is the most privileged app in the ecosystem. It runs as your user, and
from phase 1 it will stop services and write into model dirs.

## Running

```bash
./install.sh          # idempotent: venv + deps + rendered systemd user unit
stack stackui         # start (detached, via the registry)
stack down stackui    # stop
./run.sh              # or run it in the foreground
```

Then http://localhost:7863. Logs go to `server.log` (not `podman logs` — it isn't a
container). `stack status` lists it under **Control**.

## Structure

```
server.py              # FastAPI: 4 GET routes, no mutating endpoint exists
run.py                 # launcher; REFUSES to bind anything but loopback
run.sh                 # venv wrapper — what `stack stackui` invokes
install.sh             # venv + deps + systemd unit (renders @SELF@)
stack-ui.service       # systemd --user template
services/
  stackctl.py          # allowlisted `stack` invocation + output parsing + disk
  inventory.py         # models on disk, active Kobold selection (read-only)
templates/index.html · static/css/style.css · static/js/app.js
```

## Security posture

Deliberate, not incidental:

- **Loopback only.** `run.py` exits rather than bind a non-loopback address. There's no
  auth, and that's only tolerable while it can't be reached off-box. (Companion binds
  `0.0.0.0`; that pattern must not be copied here.) If remote access is ever wanted, it
  needs auth in front first — companion's `services/auth.py` drops in.
- **No shell.** Every `stack` call is a list argv to `subprocess`, never a string.
- **Allowlist, not validation.** `stackctl.VERBS` maps a key to a fixed argv. A verb that
  isn't in that dict cannot be run, so phase 0 is read-only *by construction* rather than
  by the UI merely not offering buttons. Adding phase 1's mutating verbs is a change to
  that one dict — which is exactly where the review attention should go.
- Subprocesses get a **minimal env** (HOME/PATH/XDG_RUNTIME_DIR/USER, plus AI_ECOSYSTEM
  when set) — enough for podman and systemctl to find the user session, nothing more.

## Gotchas

- **`stack status` is scraped text.** `stackctl.parse_status()` regexes the human output;
  it's the one brittle seam. Every response carries `raw`, and the UI shows the raw text
  whenever the parse yields nothing, so it fails visibly instead of rendering an empty
  page. The real fix is a `stack status --json` mode — worth doing when phase 1 needs
  structured data anyway.
- **The Kobold default filename is read out of `start.sh`** (`inventory._DEFAULT` regex)
  rather than duplicated here, so the two can't drift. If that line ever changes shape the
  UI reports "unknown" instead of guessing — deliberately no hardcoded fallback name.
- **`model.env` is parsed, never sourced** — same two-key rule as `start.sh`, for the same
  reason (it will eventually be machine-written from names fetched off the internet).
- **Excluded from `stack down all`.** The `CONTROL=(stackui)` group in `stack` is skipped
  by `down all`/`down frontends`, because otherwise the dashboard's own stop button kills
  the server rendering it. Stop it explicitly: `stack down stackui`.
- **The ComfyUI models walk is cached 30 s** (`inventory._TTL`) — it's a ~70 GB tree, and
  the dashboard polls every 10 s. Metadata only; it never opens a file.
- **`/api/doctor` is not polled.** It shells out to podman several times; it's on-demand
  behind a button.
- Deps live in `venv/` (gitignored), not on the host — the host is immutable, and
  `pip install` at user level is the thing to avoid there.

## Phase 1 (next)

Mutating verbs — `on`/`off`/`up`/`down`/`restart` — plus an SSE log tail. The work is
adding entries to `stackctl.VERBS`, a POST layer with the app name validated against the
registry rather than taken from the request, and buttons. See the plan for the rest.
