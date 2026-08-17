# stack-ui — control dashboard for the AI stack

Web frontend for the stack itself. **Phases 0–1 built:** service status, model inventory,
disk, `stack doctor` on demand, plus the control verbs (on/off/up/down/restart/start) and
a streamed log tail. Model browsing and downloads are phase 3.

Full roadmap: [`../STACK_UI_PLAN.md`](../STACK_UI_PLAN.md). This lives *inside* the ai-stack
repo — it's part of the stack, not a consumer of it, so `stack install` builds it and `stack`
reaches it via `$SELF` like the comfyui recipe.

## A container that drives the host

Every other app here is a container, and so is this one — but it controls the host, so
three mounts are load-bearing (all in `run-podman.sh`):

| Mount / flag | Why |
|---|---|
| `podman.sock` | `stack` drives podman; this is the host engine. The image ships the **podman client only** (static `podman-remote`, pinned to the host version) — this container must never run containers itself. |
| session bus (`$XDG_RUNTIME_DIR/bus`) | `stack on`/`off` are `systemctl --user`. Without it the switch can't work. |
| `--network=host` | `stack` health-probes `http://localhost:5001`. In its own netns those are the *container's* ports and everything reads down. It also makes uvicorn's `127.0.0.1` bind the host loopback, so no port is published. |
| `--userns=keep-id` | The session bus authenticates EXTERNAL against the uid the **host** sees. Under the default rootless mapping the process is uid 0 inside and 1000 outside, they disagree, and the bus answers `REJECTED`. |

Consequence: it is the most privileged app in the ecosystem — it can control the host's
containers and user services. That is the same authority the earlier host-side version
had; containerizing didn't add power, it added a filesystem boundary. It runs as your
user, stops and starts services, and from phase 2 will write into model dirs.

### The systemctl problem (and the shim)

`systemctl --user` **cannot** reach the host user manager from inside a container. It only
ever uses systemd's *private* socket (`$XDG_RUNTIME_DIR/systemd/private`), and that socket
refuses a peer from another user namespace — `ENODATA`, with or without `keep-id`, with or
without SELinux. It will not fall back to the session bus.

The session bus itself *does* accept us. So `shim/systemctl` implements the five
invocations `stack` makes (`list-unit-files`, `is-enabled`, `enable --now`,
`disable --now`, `daemon-reload`) as D-Bus calls on `org.freedesktop.systemd1`, and
shadows the real systemctl on PATH. The alternative was reimplementing `stack on`/`off`
inside the web app, duplicating logic `stack` is supposed to own. Anything outside those
verbs exits 2 rather than pretending to work.

## Running

```bash
stack install stackui      # build the image (part of `stack install`)
stack stackui              # start
stack down stackui         # stop
./run-podman.sh --rebuild  # rebuild after a code change (code is baked into the image)
stack logs stackui         # podman logs -f
```

Then http://localhost:7863. `stack status` lists it under **Control**.

## Structure

```
server.py              # FastAPI routes + the Host/Origin guards
run.py                 # launcher; REFUSES to bind anything but loopback
Containerfile          # python + podman CLIENT + the systemctl shim
run-podman.sh          # the run flags, each one load-bearing — read the header
shim/systemctl         # systemctl -> D-Bus translation (see above)
services/
  stackctl.py          # THE security boundary: verb allowlist, target validation, exec
  jobs.py              # single-slot action runner (one mutation at a time)
  inventory.py         # models on disk, active Kobold selection (read-only)
templates/index.html · static/css/style.css · static/js/app.js
```

## How an action runs

`POST /api/action {verb, target}` → `jobs.start()` → `stackctl.resolve()` validates →
thread runs `stackctl.stream()` → browser follows `GET /api/action/<id>/events` (SSE).

- **One action at a time**, refused (409) rather than queued — a mutation that fires
  minutes later, after you've changed your mind, is worse than an error.
- **Output is replayed from the start** on connect, so a reload or a second tab sees the
  whole run instead of joining mid-flight. That's also why the job keeps its lines rather
  than using a queue.
- **`stack up` can take a couple of minutes** on a cold model load, which is why actions
  stream instead of holding a request open.

## Security posture

Deliberate, not incidental:

- **Loopback only.** `run.py` exits rather than bind a non-loopback address. There's no
  auth, and that's only tolerable while it can't be reached off-box. (Companion binds
  `0.0.0.0`; that pattern must not be copied here.) If remote access is ever wanted, it
  needs auth in front first — companion's `services/auth.py` drops in.
- **Host + Origin guards, and a required `X-Stack-UI: 1` header on writes.** Without auth
  the browser is a confused deputy: any page you have open can POST to localhost. The Host
  check blocks DNS rebinding; the custom header can't be set by a cross-origin form POST,
  and anything that could set it is forced through a preflight we never answer. Not a
  substitute for auth — it's what makes loopback-only defensible now that POST *does*
  something.
- **No shell.** Every `stack` call is a list argv to `subprocess`, never a string.
- **Allowlist, not validation.** `stackctl.VERBS` maps a key to a fixed argv. A verb that
  isn't in that dict cannot be run. **This table is the security boundary** — a new entry
  is the one change that can widen what the web is allowed to do, so it's where review
  attention belongs.
- **Targets come from the registry, not the request.** An app name is accepted only if
  `stack status` currently lists it; an empty registry refuses rather than falling back to
  trusting the request. `down` additionally accepts the group names all/backends/frontends.
- **It won't act on itself.** `stackui` is rejected as a target for mutating verbs — a
  `restart stackui` through the UI kills the server mid-request, leaving the page waiting
  on a reply that can never arrive. Stop it from a terminal.
- Subprocesses get a **minimal env** (HOME/PATH/XDG_RUNTIME_DIR/USER, plus AI_ECOSYSTEM
  when set) — enough for podman and systemctl to find the user session, nothing more.
- Children run in **their own process group**, and closing an SSE stream kills the group.
  `stack logs` execs `podman logs -f`, which never exits on its own; killing only the
  parent would leave it attached to the pipe forever.

## Gotchas

- **`stack status` is scraped text.** `stackctl.parse_status()` regexes the human output;
  it's the one brittle seam. Every response carries `raw`, and the UI shows the raw text
  whenever the parse yields nothing, so it fails visibly instead of rendering an empty
  page. The real fix is a `stack status --json` mode — worth doing whenever a later phase
  needs structured data anyway.
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
- **`CONTAINER_HOST` and `DBUS_SESSION_BUS_ADDRESS` must be in `stackctl._PASSTHROUGH`.**
  The subprocess env is a deliberate allowlist, and dropping either fails *quietly*:
  without the first, podman talks to an engine that isn't there and every up/down does
  nothing; without the second, the switch silently disappears from `stack status`.
- **The ecosystem is mounted at its REAL host path, never a tidy `/eco`.** With a remote
  client, `-v` and build-context paths are resolved by the *host* engine. A path that
  exists only inside this container produces containers with broken mounts — and it fails
  quietly: `stack iguana` returned exit 0 while starting nothing usable. Identical paths on
  both sides is what makes starting other apps work at all.
- **Three `stack doctor` checks can't pass from in here** and are not real failures:
  `nvidia-smi` and the CDI spec need host NVIDIA tooling, and the systemd-unit check looks
  for a *file* under `$HOME`. Run `stack doctor` in a terminal for those; the rest (images,
  network, custom-node pins) are accurate.
- **The podman client is version-pinned** to the host engine (`PODMAN_VERSION` in the
  Containerfile). Client/server API skew across majors is a real failure mode; bump it
  when the host's podman moves.

- **Generators validate late.** `stackctl.stream()` is a generator function, so nothing in
  its body — including the target check — runs until the first iteration. A route that
  builds the generator and returns it has already sent `200` plus SSE headers by then, so
  a bad app name surfaced as an empty 200 stream instead of a 400. The logs route now
  calls `resolve()` itself before constructing the stream. Any future streaming route
  must do the same.
- **`stack logs stackui` is refused** with an explanation: `cmd_logs` is `podman logs -f`
  and this app isn't a container. Its log is `server.log` beside the code.
- **The UI disables every button while an action runs**, which is what keeps the
  single-slot 409 from being something a user can trip over by accident.

## Writes (phase 2)

`POST /api/kobold/select` writes `koboldcpp/model.env`; `DELETE /api/models/{kind}/{name}`
removes a model file. Both go through `inventory._safe_target()`, which is the only way to
name a file in this app:

- the collection is a **key** (`kobold`/`checkpoint`/`lora`/`vae`) mapped to a directory —
  a request never supplies a path;
- the name must have no separators and must carry the right extension;
- after resolution the parent must still be exactly that directory, so a symlinked models
  dir or a crafted name can't land the write elsewhere;
- the file must already exist.

**Selecting does not restart.** A restart drops what's loaded and costs 20–30 s of model
load; doing that as a side effect of clicking a name is a surprise the UI shouldn't spring.
The inventory exposes `running` (from KoboldCPP's own API) and `restart_needed`, and the
UI shows a pending banner whose button fires the ordinary `restart koboldcpp` action.

**Deleting refuses two cases**: the currently *selected* model (start.sh hard-fails on a
missing selection, so it would break the backend at the next start rather than now) and
the currently *loaded* one. `model.env` is written atomically — a torn write would be
parsed by start.sh at the next boot.

## Browsing + downloads (phase 3)

`services/hub.py` searches HuggingFace (huggingface.co can't be iframed, so this is our
own UI over their JSON API); `services/downloads.py` fetches into the directory a backend
actually reads.

**No `huggingface_hub` dependency**, reversing the plan's lean. The download loop has to
be hand-written anyway for byte-level progress, the library's cache would put files in a
HF-shaped cache dir rather than the models dir the backends scan, and this is a
control-plane container where a small dependency surface is worth more. Three URLs total.

- **Fit hints are advisory and say so.** VRAM comes from ComfyUI's `/system_stats` — there
  is no NVIDIA tooling in here — and when ComfyUI is down the hint is *suppressed*, not
  guessed. `STACK_UI_VRAM_BYTES` overrides. Bands account for an SD 1.5 checkpoint
  alongside, which is the pairing both INSTALL guides recommend.
- **One download at a time**, refused (409) rather than queued — same reasoning as
  actions. Two 7 GB transfers over one link finish later than one after the other.
- **`.part` + atomic rename.** Bytes land in `<name>.part` and are renamed only after a
  length check, so a killed transfer can never look like a usable model to a backend
  scanning that directory. Cancelling *keeps* the `.part` — throwing away 6 GB at 90% is
  worse than a resumable stub — and a restart resumes with a `Range` request. If the
  server ignores `Range` and answers 200, the old bytes are discarded rather than appended.
- **Overwrite is refused.** `safe_new_target()` rejects a name that already exists: a
  download silently replacing a model a backend may have loaded is the worst surprise
  available here.
- **The remote filename is basenamed** before it is joined to anything, so `../../x.gguf`
  names `x.gguf` inside the collection rather than escaping it — and the resolved parent
  is re-checked anyway.
- **Auto-activate only writes `model.env`.** It never restarts a backend as a side effect
  of a transfer finishing.
- `HF_TOKEN` is picked up from the environment for gated repos.

## Phase 4 (next)

Civitai for ComfyUI checkpoints, then LoRA/VAE targets. See `../STACK_UI_PLAN.md`.
