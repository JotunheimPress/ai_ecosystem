# stack-ui — the AI Ecosystem control dashboard

Web frontend for the stack itself. **Phases 0–5 built:** service status and control, streamed
logs, model inventory with selection and delete, HuggingFace + Civitai browsing and downloads,
and a VRAM planner across the backends (KoboldCPP's layers around a ComfyUI checkpoint and
Fortuna's model).

**pytincture + wapyt since 2026-10-26.** The page is Python in the browser (Pyodide) built
from wapyt widgets — the same stack as the Pytinc apps (`~/Development/Pytinc/basilisk_wapyt`
is the closest sibling; its and Monguana's CLAUDE.md files document the framework traps).
The vanilla-JS page and the JSON API it called are gone; `services/` carried over unchanged
apart from moving into `appcode/`. Layout: a header toolbar (whether the backends run,
Start/Stop, the separate **Start at boot** toggle, and the **workspace switch**), a sidebar of screens, and the screen. Two workspaces
(since 2026-11-19):

- **Infrastructure** (`#/infrastructure`, the landing page): the router (where each
  capability is served, the text route control), one card per **capability** (text, image
  and sound, decision) with what it has loaded, and the GPU. Its sidebar screens: language
  models (installed GGUFs + HuggingFace search + Fortuna's models), image and audio models (ComfyUI files +
  Civitai search), VRAM planner; plus logs, custom nodes and doctor under System.
- **Frontends** (`#/frontends`): the installed apps, with HTTPS links where a frontend offers
  them, an **Official / Custom** label, **Update** and **Remove**, the `frontend.json` problems
  `stack` found, and "Stop all frontends"; then **Available**: the official frontends not
  installed yet, each with **Install** (or why it can't be). See *Installing frontends* below.

**Themes** (since 2026-11-19, like Whimsy's): swatches under the sidebar pick one of Whimsy's five
dark colourways (Bone, Ember, Moss, Dusk, Harbor, same palettes) or Daylight. `ui/themes.py` sets
`<html data-sutheme>` and wapyt's dark or light mode. Every colour in `static/stackui.css` is a
variable from that attribute, and wapyt's own tokens (and the accents it hard-codes in blue:
toolbar, sidebar, tabs, table selection, dialogs, progress) are pointed at the same variables, so
one attribute restyles the page. The choice is per browser (localStorage `stackui.theme`, guarded);
with none stored the page follows the OS (dark → Harbor, light → Daylight). The smoke test clicks
through every theme. wapyt gives toolbar text no id, so `stackui.py` tags the brand and the
running state with `data-su` for the CSS.

Screens route by URL hash, so a reload keeps your place; the old `#/dashboard` lands on
Infrastructure. Toolbar actions apply to the backends ("Stop backends" leaves frontends
running); stopping or restarting the router asks first and names what goes offline.

Full roadmap: [`../STACK_UI_PLAN.md`](../STACK_UI_PLAN.md). This lives *inside* the ai-stack
repo — it's part of the stack, not a consumer of it, so `stack install` builds it and `stack`
reaches it via `$SELF` like the comfyui recipe.

**Where the checkout is** (`stackctl.CHECKOUT`): `STACK_CHECKOUT` (set by `run-podman.sh`, since
the image's copy of the code isn't inside the checkout), else the checkout this code runs from
(the dev server), else `<ecosystem>/ai-stack`. Never assume the folder is called `ai-stack`:
the GitHub repo is `ai_ecosystem`, and a plain `git clone` names the folder after it.
`ECO` (the ecosystem root) is `AI_ECOSYSTEM`, else the checkout's parent.

**Naming.** The facing name is **AI Ecosystem** — the browser tab title, the toolbar brand,
the `stack status` description, and the startup banner all say that. Everything an
operator *types* is unchanged and must stay unchanged: the `stackui` service key (`stack stackui`,
`stack down stackui`, `stack install stackui`), the `localhost/stackui` image, this `stack-ui/`
directory, and the pytincture application name `stackui` (the page is `/stackui/`; `/`
redirects there). Rename labels, never identifiers. AI Ecosystem is also the whole product's brand now
(`../CLAUDE.md` → *Branding*); the toolbar shows its release beside the name (`_show_release`,
from `release` in `stack status --json`), with the checkout's commit in the tooltip.

## A container that drives the host

Every other app here is a container, and so is this one — but it controls the host, so
three mounts are load-bearing (all in `run-podman.sh`):

| Mount / flag | Why |
|---|---|
| `podman.sock` | `stack` drives podman; this is the host engine. The image ships the **podman client only** (static `podman-remote`, pinned to the host version) — this container must never run containers itself. |
| session bus (`$XDG_RUNTIME_DIR/bus`) | `stack boot on|off` is `systemctl --user`. Without it the Start at boot toggle can't work. |
| `--network=host` | `stack` health-probes `http://localhost:5001`. In its own netns those are the *container's* ports and everything reads down. It also means uvicorn binds the host's own interfaces (`0.0.0.0`, the whole LAN), so no port is published. |
| `--userns=keep-id` | The session bus authenticates EXTERNAL against the uid the **host** sees. Under the default rootless mapping the process is uid 0 inside and 1000 outside, they disagree, and the bus answers `REJECTED`. |
| `-v` host `nvidia-smi` + `libnvidia-ml.so.1`, `--device /dev/nvidiactl /dev/nvidiaN` | The **host's** `nvidia-smi` (it must match the host driver, so it isn't installed in the image). `koboldcpp/start.sh` reads free VRAM with it to wait out a model swap; without it those waits silently skip. Creates no CUDA context, so no VRAM cost. Skipped with a warning if either file is missing on the host. **Not CDI** (`--device nvidia.com/gpu=all`, used until 2026-11-24): under `--userns=keep-id` podman 6.2 id-maps every CDI mount (`ridmap`), the kernel refuses that on Bazzite's composefs root, and the container could not start (`crun: mount_setattr /usr/bin/nvidia-cuda-mps-control: Operation not permitted`). Because it has a restart policy, that failure also failed `podman-restart.service` at boot. Plain `-v` binds are not id-mapped. |

Consequence: it is the most privileged app in the ecosystem — it can control the host's
containers and user services. That is the same authority the earlier host-side version
had; containerizing didn't add power, it added a filesystem boundary. It runs as your
user, stops and starts services, writes into model dirs, and fetches files off the internet.

### The systemctl problem (and the shim)

`systemctl --user` **cannot** reach the host user manager from inside a container. It only
ever uses systemd's *private* socket (`$XDG_RUNTIME_DIR/systemd/private`), and that socket
refuses a peer from another user namespace — `ENODATA`, with or without `keep-id`, with or
without SELinux. It will not fall back to the session bus.

The session bus itself *does* accept us. So `shim/systemctl` implements the five
invocations `stack` makes (`list-unit-files`, `is-enabled`, `enable --now`,
`disable --now`, `daemon-reload`) as D-Bus calls on `org.freedesktop.systemd1`, and
shadows the real systemctl on PATH. The alternative was reimplementing `stack boot`
inside the web app, duplicating logic `stack` is supposed to own. Anything outside those
verbs exits 2 rather than pretending to work.

## Running

```bash
stack install stackui      # build the image if missing or stale, restart a running dashboard on it
stack stackui              # start (rebuilds first if the image is stale)
stack down stackui         # stop
./run-podman.sh --rebuild  # force a build; --build-only builds if needed and starts nothing
stack logs stackui         # podman logs -f
```

**Stale images are rebuilt** (since 2026-11-20). The code is baked into the image, and until
then `stack install` only checked that an image *existed*, so after a `git pull` it kept the old
one and the old dashboard: a collaborator saw Fortuna but the old interface. `source-hash.sh`
hashes the Containerfile and everything the build context takes in (exclusions follow
`.containerignore`); `run-podman.sh` stamps it on the image as the label `ai-stack.source` and
rebuilds when it differs. `stack install` does the same through `run-podman.sh --build-only`,
then recreates a running dashboard, except from inside the dashboard itself (`/run/.containerenv`),
where that would kill the command; `stack doctor` reports a stale image. A file added to the image
outside `stack-ui/` would need adding to the hash.

Then http://localhost:7863 (redirects to `/stackui/`), or `http://<host>.local:7863` from any device on the LAN. `stack status` lists it under **Control**.
The image clones wapyt from GitHub at the pinned `WAPYT_REF` (the Containerfile header says
how to build against the local checkout instead).

### Developing without the container

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python "pytincture==1.0.0" "uvicorn[standard]" jeepney pytest pip \
    ~/Development/Pytinc/wa_pytincture_widgetset
PYTHON=$PWD/.venv/bin/python ~/Development/Pytinc/wa_pytincture_widgetset/scripts/dev_wheel.sh appcode
STACK_UI_PORT=7873 .venv/bin/python service.py      # 7863 is the running container's
.venv/bin/python -m pytest -q tests                  # backends.py against fake APIs; BFF statelessness
STACKUI_URL=http://127.0.0.1:7873 CHROME=~/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome \
  uv run --with playwright python tests/smoke.py    # every screen, fails on any console error
```

The host-side server drives the host's podman and systemctl directly — no shim needed — so it
is a real control plane too, not a mock. The smoke test only reads (plus one HuggingFace
search; `STACKUI_OFFLINE=1` skips it).

## Structure

```
service.py             # ASGI entry: create_app + the GET event streams; binds 0.0.0.0 (STACK_UI_HOST)
Containerfile          # python 3.13 + wapyt wheels + podman CLIENT + the systemctl shim
run-podman.sh          # the run flags, each one load-bearing — read the header
shim/systemctl         # systemctl -> D-Bus translation (see above)
tests/                 # test_backends.py, test_gpu.py, test_status.py, test_context.py,
                       #   test_fortuna.py (pytest), smoke.py (Playwright)
appcode/               # pytincture modules_path
  stackui.py           # the page: toolbar, sidebar, routing, action console, download strip
  ui/                  # browser-only screens (pytincture ships what the page imports)
    common.py          #   spawn, esc, toast, failed(), Stream (EventSource), Listeners
    themes.py          #   the theme swatches: Whimsy's five colourways + Daylight
    dashboard.py       #   InfrastructureView (router + capability cards + GPU), FrontendsView
    llms.py            #   Language models: Installed + Find on HuggingFace + Fortuna
    fortuna_models.py  #   its Fortuna tab: models.table rows, integrity, select/download/verify
    images.py          #   Image models: Installed + Find on Civitai + Find on HuggingFace
    vramview.py        #   VRAM planner
    system.py          #   Logs, Custom nodes, Doctor
    about.py           #   About: release, update check, how to update, project links
  static/stackui.css   # served at /static (no-cache, so a rebuild's CSS shows on next load);
                       #   the palettes and wapyt's tokens mapped onto them
  wapyt-99.99.99-*.whl # the BROWSER's wapyt (git-ignored; dev_wheel.sh / the Containerfile)
appcode/services/
  stack_api.py         # BFF: overview, action, current_job, doctor, nodes, latest_release
  about.py             # project links + version comparison — BOTH sides (the page imports it)
  release_check.py     # is a newer release out? GitHub's API, cached; GITHUB_TOKEN for the private repo
  models_api.py        # BFF: llms, images, select/delete, vram/plan/free, HF + Civitai, downloads
  backends.py          # what KoboldCPP / ComfyUI have loaded right now (see below)
  stackctl.py          # THE security boundary: verb allowlist, target validation, exec
  jobs.py              # single-slot action runner (one mutation at a time)
  inventory.py         # models on disk, selection, delete, per-model layer presets
  hub.py               # HuggingFace search + fit hints; image-file classifier + header inspect
  civitai.py           # Civitai search (checkpoints / LoRAs / VAEs)
  downloads.py         # transfers: progress, resume, host allowlist
  provenance.py        # where each downloaded model came from (model-sources.json)
  vram.py              # GGUF header parsing, live VRAM, the planner
  gpu.py               # live GPU figures from the host: amdgpu sysfs, nvidia-smi, then ComfyUI
  nodes.py             # custom-node pins vs checkouts
```

## pytincture: how the page talks to the server

- **BFF calls** for everything request/response, mutations included:
  `await StackApi().action_async(verb, target)`. Every method returns `{"ok": ...}` and
  never raises for an expected failure; `ui.common.failed()` toasts the error.
- **Plain GET event streams** in `service.py` for what outlives a request:
  `/api/action/<id>/events`, `/api/logs/<app>/events`, `/api/downloads/<id>/events`. A BFF
  call is bounded (~35 s in the browser) and `stack up` or a log tail is not. They only read;
  closing one kills what it follows. `ui.common.Stream` wraps `EventSource` and stops on the
  first error instead of letting it auto-reconnect (these streams are finite; a reconnect
  would just replay).
- `/api/health` stays where it was: `stack status` probes it.

Traps hit while building it:

- **BFF modules are re-executed on every call.** All state (the job slot, the download,
  caches) lives in the plain `services/*` modules, which the BFFs import *inside* methods.
  `tests/test_backends.py` fails on a module-level assignment or call in a BFF module.
  `service.py` puts `appcode` on `sys.path` so the stream routes and the BFFs share one
  `services` package — otherwise a job started by a BFF call is invisible to its stream.
- **No `@bff_policy`.** There is no login, and any `@bff_policy` makes pytincture require a
  policy hook at startup. Don't add one without adding auth.
- **`get_cell()` returns wapyt's cell object, not an element** — use
  `ui.common.container_of(cell)` (`getContainer()`) before `appendChild`/`innerHTML`.
- **`[hidden]` loses to `display:`** — `.su-btn[hidden]` is restated in the CSS; any new
  class that sets `display` on something toggled with `hidden` needs the same.
- **Destroying a proxy from inside its own callback fails** — `Stream.close()` releases its
  proxies on the next tick, because it is usually called from its own "done" handler.
- Import *names* in browser code (`from ui.common import spawn`), never modules — the
  packager only follows the first form. DOM misses are `JsNull` (falsy, not `None`).

## How an action runs

`StackApi.action(verb, target)` (BFF) → `jobs.start()` → `stackctl.resolve()` validates →
thread runs `stackctl.stream()` → the page follows `GET /api/action/<id>/events` (SSE) in
the console panel at the bottom.

- **One action at a time**, refused (409) rather than queued — a mutation that fires
  minutes later, after you've changed your mind, is worse than an error.
- **Output is replayed from the start** on connect, so a reload or a second tab sees the
  whole run instead of joining mid-flight. That's also why the job keeps its lines rather
  than using a queue.
- **`stack up` can take a couple of minutes** on a cold model load, which is why actions
  stream instead of holding a request open.

## Security posture

Deliberate, not incidental:

- **The whole LAN, no auth** (since 2026-10-30). The stack is a local product for whoever
  installs it, so the dashboard answers on `0.0.0.0:7863` like every app; anyone on the home
  network can drive it. The frontend cards link to `http://<the page's own host name>:<port>`,
  so a phone's links point at the stack's host, not at the phone.
- **Origin guards on every write.** Without auth the browser is a confused deputy: any page
  you have open can POST to the dashboard. These come from pytincture itself rather than our
  middleware (there is no `allowed_hosts` list any more): a BFF write must be
  `application/json` (a cross-origin form can't send that, and anything that can is forced
  through a CORS preflight nobody answers) and must carry a same-origin `Origin` /
  `Sec-Fetch-Site`. That replaces the old `X-Stack-UI: 1` header, which gave the same
  guarantee. Verified 2026-10-26: cross-origin JSON → 403 "Origin validation failed", a
  form post → 415; re-checked 2026-10-30 from the LAN address (cross-origin → 403). The
  event-stream routes are GET-only and read-only.
- **No shell.** Every `stack` call is a list argv to `subprocess`, never a string.
- **Allowlist, not validation.** `stackctl.VERBS` maps a key to a fixed argv. A verb that
  isn't in that dict cannot be run. **This table is the security boundary** — a new entry
  is the one change that can widen what the web is allowed to do, so it's where review
  attention belongs.
- **Targets come from the registry, not the request.** An app name is accepted only if
  `stack status` currently lists it; an empty registry refuses rather than falling back to
  trusting the request. `down` additionally accepts the group names all/backends/frontends.
- **Fortuna's model verbs take a table name, nothing else.** `fortuna_fetch` / `fortuna_verify`
  run `stack fortuna-model fetch|verify NAME`, and `resolve()` accepts NAME only if
  `fortuna/models.table` lists it. The recipe maps it to the file, the pinned URL and the hash, so
  the browser can't name a file, a URL or a commit.
- **A git URL only from the local network** (since 1.5.0). `frontend_add_url` (`stack frontend
  add --yes URL`) is the one target that comes from the request, and what it installs runs code
  nobody reviewed, with this container's control of the host. So `resolve()` takes it only when
  `stackctl.REQUEST_LAN` is set, and only a plain git URL (`GIT_URL`, which mirrors `stack`'s
  own check). `service.py`'s `MarkLanRequests` (plain ASGI middleware) sets it per request from
  `is_lan_request`: a client on a **LAN address** (loopback, private, link-local, or Tailscale's
  100.64/10; not a port forwarded from the internet) that asked for a **LAN Host**: an IP
  address, `localhost`, a single label (`amd-halo`), or a `.local` / `.lan` / `.home.arpa` /
  `.internal` name. The Host check is what stops DNS rebinding: a web page open on any LAN
  machine can point its own domain at the dashboard and pass the same-origin check, but its
  Host still names that domain. The LAN itself is trusted, as for every other action (the
  stack runs on Halo and is used from Predator). BFF calls run in a copy of the request's
  context, and `jobs.start` runs its worker in one too (`contextvars.copy_context`), or
  `stream()`'s second `resolve()` would refuse. With host networking and no proxy the client
  address is the real peer. **A reverse proxy on this machine in front of the dashboard would
  make every request look like the LAN**: don't, or drop this verb.
- **The text route is two fixed verbs**, `route_text_local` and `route_text_halo`, with no
  target: the browser picks one of them, never a host. Halo's address comes from
  `ai-stack/halo.env` on the stack's side.
- **It won't act on itself.** `stackui` is rejected as a target for mutating verbs — a
  `restart stackui` through the UI kills the server mid-request, leaving the page waiting
  on a reply that can never arrive. Stop it from a terminal.
- Subprocesses get a **minimal env** (HOME/PATH/XDG_RUNTIME_DIR/USER, plus AI_ECOSYSTEM
  when set) — enough for podman and systemctl to find the user session, nothing more.
- Children run in **their own process group**, and closing an SSE stream kills the group.
  `stack logs` execs `podman logs -f`, which never exits on its own; killing only the
  parent would leave it attached to the pipe forever.

## Gotchas

- **Status is `stack status --json`** (`stackctl.status()`, stdout only, since 2026-11-19).
  It carries each backend's `capability`, its `local` copy state, the text `route` and the
  manifest `warnings`. A stack without it falls back to `parse_status()` scraping the text,
  and the UI shows the raw text whenever that parse yields nothing.
- **Cards follow capability, not name.** `services/backends.ADAPTERS` maps a capability to
  the adapter whose figures fill its card (`kobold`, `comfy`, `fortuna`). Fortuna's adapter
  reads `/health` (it names the model), not `/v1/models`, which needs the API key when set.
- **With text routed to Halo, `localhost:5001` is the Halo box.** `inventory.kobold()` reads
  the route from `$ECO/router/route.conf` and then reports no "restart to apply": a restart
  of the local copy can't change what Halo serves. The text card's buttons say "local copy",
  and its state comes from `local` in the status JSON.
- **`run-podman.sh` checks the podman socket before removing the running dashboard**, so a
  missing socket leaves the old one up instead of no dashboard at all.
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
- **The doctor is not polled.** It shells out to podman several times; it's on-demand
  behind a button.
- **`STACK_GPU` must be in `stackctl._PASSTHROUGH` too** (2026-10-30). `run-podman.sh` resolves
  the host profile (`lib/host.sh`) on the host and passes `-e STACK_GPU`, because the start
  scripts detect the GPU with `nvidia-ctk` / `/dev/kfd`, which this container doesn't have. Drop
  it and a KoboldCPP restarted from the dashboard comes up **with no GPU**, without an error.
- **`CONTAINER_HOST` and `DBUS_SESSION_BUS_ADDRESS` must be in `stackctl._PASSTHROUGH`.**
  The subprocess env is a deliberate allowlist, and dropping either fails *quietly*:
  without the first, podman talks to an engine that isn't there and every up/down does
  nothing; without the second, start at boot silently disappears from `stack status`.
- **Mount the podman socket's directory, never the socket file.** A file bind-mount pins the
  inode; when `podman.socket` re-creates the socket, the container keeps the dead one and every
  action fails with `dial unix /run/podman/podman.sock: connection refused` while the page
  itself still loads (status is read by health probes, not podman). Hit on 2026-09-23.
- **The ecosystem is mounted at its REAL host path, never a tidy `/eco`.** With a remote
  client, `-v` and build-context paths are resolved by the *host* engine. A path that
  exists only inside this container produces containers with broken mounts — and it fails
  quietly: `stack iguana` (now `stack iguana_studio`) returned exit 0 while starting nothing usable. Identical paths on
  both sides is what makes starting other apps work at all.
- **Two `stack doctor` checks can't pass from in here** and are not real failures:
  the CDI spec needs host `nvidia-ctk`, and the systemd-unit check looks
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
- **The UI disables every button while an action runs**, which is what keeps the
  single-slot 409 from being something a user can trip over by accident.

## Writes (phase 2)

`ModelsApi.select_llm` writes `koboldcpp/model.env`; `ModelsApi.delete(kind, name)` removes a
model file. Both go through `inventory._safe_target()`, which is the only way to
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
UI shows a pending banner (on the dashboard's Oracle card and the Language models screen)
whose button fires the ordinary `restart koboldcpp` action. Selecting from the Language
models screen *offers* the restart in a confirm dialog; it still never happens by itself.

**Context window** (Language models → Installed, the combobox under the status line):
`ModelsApi.set_llm_context` writes `KOBOLDCPP_CONTEXT` into `model.env` (removing the line =
KoboldCPP's default, 8192), keeping every other line as it is; `select_llm` carries it over
on a model change. Sizes are `inventory.CONTEXT_SIZES` — 4K, 6K, 8K, 12K, 16K, 24K, 32K, 48K, 64K, 96K, 128K —
and must match the `case` in `koboldcpp/start.sh` (`tests/test_context.py` checks). Each
option shows the KV-cache cost for the selected model (`vram.kv_cache_bytes`, f16, from the
GGUF header's attention shape; hybrid models count only every `full_attention_interval`th
layer, so Qwen3.5 is a quarter of a dense model) and is flagged past the model's trained
`context_length`. Like a model change it never restarts: `restart_needed` is also set when
the running context (`/api/extra/true_max_context_length`) differs from the selection, and
`restart_for` says which (`model`/`context`), so the banners name the right reason.

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

- **Fit hints are advisory and say so.** The GPU total comes from `services/gpu.py` (host
  counters, then ComfyUI); when nothing is readable the hint is *suppressed*, not guessed.
  `STACK_UI_VRAM_BYTES` overrides. Bands account for an SD 1.5 checkpoint
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
- **Every result links out to its own page**: the repo on huggingface.co ("model card"
  above the file list), the model — and each *version* ("Version page") — on civitai.com.
  The tables show sizes and quants, never the model card, so the licence, the prompt
  format and the example images are one click away instead of unreachable.
- `HF_TOKEN` is picked up from the environment for gated repos.
- **Tokens reach the container through `run-podman.sh`** — `stack-ui/tokens.env`
  (`CIVITAI_TOKEN=…` / `HF_TOKEN=…` lines, git-ignored, kept out of the image, `chmod 600`)
  via `--env-file`, or the same names from the calling shell via `-e NAME` (no value on the
  command line either way). Until 2026-10-26 nothing passed them in, so the containerized
  dashboard could never download a gated file: a Civitai SDXL VAE came back `401`. A 401/403
  now says which variable to set, or that the one sent lacks access.

## Provenance — linking a file on disk back to its page

The search results link out for free: a result *is* a remote item. An inventory row is a
directory listing — a name and a size — and nothing about a `.gguf` in `models/` says which
repo it came from. So `services/provenance.py` writes it down at the moment we fetch it,
and `inventory.kobold()` / `inventory.checkpoints()` join on the filename.

- **Recorded, never guessed.** The store is `$ECO/model-sources.json` — the ecosystem root,
  because it spans both backends' model directories — keyed `"<collection>/<filename>"`,
  written atomically like `model.env`. Gitignored by living outside any checkout.
- **Only what this app downloaded has an entry.** Most of the 85 GB was copied in by hand
  and always will have none, so the UI falls back to a **search** link that says *search*.
  A link that claims to be the model's page and lands on a list of near-matches is worse
  than no link, so the two are never presented as the same thing.
- **The Civitai page URL needs the model id**, which only the `/model-versions/{id}`
  response carries — a version id alone has no public page. `resolve_file()` therefore
  returns it as a third element rather than having the downloader rebuild it from something
  the browser sent.
- **Written after the atomic rename, before `invalidate()`.** After, so a record can never
  describe a `.part` that never completed; before, so the next listing to repopulate the
  30 s checkpoint cache already sees it.
- **`delete_model` forgets the entry**, or a later file of the same name would inherit a
  link to something it isn't.
- **The download panel links out too**, from `Download.page` — it is the one place that
  knows the origin for certain, since it's the URL being fetched right now, so the link is
  shown whether the transfer succeeds or fails. The failure case is the more useful of the
  two: the page is where you find out the repo is gated or the file has been pulled.
- The store is a cache of provenance, not a source of truth about what exists: missing,
  unreadable or corrupt degrades to "no entry" and never blocks a listing, and a failed
  write never turns a finished 7 GB download into an error.

## Fortuna's models (Language models → Fortuna)

Fortuna doesn't load whatever is in its directory: it serves only the rows of
`ai-stack/fortuna/models.table`, each pinned by sha256 to one Hub commit with its own
calibration temperatures. So the tab lists every row, downloaded or not, and the column that
matters is **integrity** — read from the stamps `start.sh` writes, never computed here
(`fortuna/CLAUDE.md` → *Verification*).

- **Use** writes `MODEL=` into `$ECO/fortuna/fortuna.env` (`inventory.select_fortuna`), keeping
  every other line (API_KEY…), atomically. It refuses a name not in the table, a file that isn't
  downloaded and one that failed its sha256 — `start.sh` would refuse them at the next start,
  which may be a boot. An unchecked file asks first. It never restarts: `restart_needed` compares
  the selection with the model `/health` names, and the tab and the decision card offer the restart.
- **Download / Verify are stack actions**, streamed in the console: the downloader is the recipe's
  own (`curl` into a `.part`, a progress line every 5 s because curl's `\r` meter shows as nothing
  in the console), not `downloads.py` — the pinned URL and the hash live in one place.
- **Delete** goes through `_safe_target("fortuna", file)` like every delete, refuses the file
  Fortuna boots or has loaded, and removes its stamps.
- **The default** (no `MODEL=`) depends on the GPU, as in `start.sh`: `inventory.host_gpu()` reads
  `STACK_GPU` (set by run-podman.sh), then `host.env`, then a coarser detection than
  `lib/host.sh` (a CDI spec → NVIDIA, `/dev/kfd` → AMD) for the host-side dev server.
- **VRAM planner:** Fortuna's boot model is counted by default — its file plus the KV cache of its
  16,384-token context (`vram.fortuna_bytes`) — and can be set to "not counted".

## HuggingFace image models

The Images screen's third tab: HuggingFace repos → `.safetensors` files → a ComfyUI folder.
Same downloader as the LLM side (`downloads.start_hf`), with a destination `kind`.

- **Five image collections now** (`inventory.IMAGE_KINDS`): checkpoint, lora, vae, plus
  `diffusion_model` → `diffusion_models/` and `text_encoder` → `text_encoders/` for split
  models (Flux, SD3.5, Wan…). The legacy `unet/`/`clip/` aliases are listed, never written.
- **Search is unfiltered by default.** ComfyUI repacks (`Comfy-Org/…`, the text-encoder
  repos) carry no pipeline tag and no `safetensors` library tag — measured: every filter
  returned 0 for `Comfy-Org/flux1` — so a filter hides exactly the repos that suit ComfyUI.
  "Tagged for ComfyUI" (`filter=comfyui`) and the pipeline tags are opt-in narrowers.
- **`hub.image_file()` refuses** diffusers parts (a top-level folder of a repo with
  `model_index.json`), shards (`-0000N-of-0000M`) and generic names
  (`model*.safetensors`, `diffusion_pytorch_model*`), which would be meaningless once
  basenamed and collide across repos. `download_hf` re-checks this server-side.
- **The filename can't tell a checkpoint from a bare diffusion model** — BFL's and
  Comfy-Org's `flux1-schnell.safetensors` share name and size. So selecting a file runs
  `hub.inspect()`: two byte-range requests for the safetensors header (8-byte length +
  JSON tensor index, capped at 16 MB; a server answering 200 instead of 206 is refused
  rather than streamed), then `kind_from_tensors()` on the key prefixes
  (`model.diffusion_model.` + `first_stage_model.`/`conditioner.` = all-in-one, bare
  `double_blocks.` etc. = diffusion model, any "lora" in a key = LoRA…). Cached per
  (repo, path). Gated without `HF_TOKEN` → 401 → the filename guess stays, marked "(guess)".
- **No VRAM hint** for image files — size says little about what an image model needs.

## Civitai (phase 4)

`services/civitai.py` covers what ComfyUI loads — checkpoints, LoRAs, VAEs — since that's
where `comfyui/INSTALL.md` §8's list actually came from. Same stdlib-only shape as `hub.py`.

- **The nesting is preserved**, not flattened: Civitai keeps model → version → file, and
  picking the *version* (SD 1.5 vs SDXL, v1.8 vs v2) is the real decision. A flat file list
  would hide the thing you came to choose.
- **The browser never sends a URL.** It sends a version id and a filename; `resolve_file()`
  re-fetches the version from Civitai and takes the download URL from there. A client that
  could hand the downloader an arbitrary URL would turn this into a fetch-anything proxy —
  a much worse thing to have listening on your machine than a model browser.
- **`downloads.ALLOWED_HOSTS`** refuses anything not on huggingface.co or civitai.com
  regardless, as the belt to that braces.
- `CIVITAI_TOKEN` is sent as a bearer header when set — never as a query parameter, which
  would land in logs and shell history. Search needs no key; many downloads now do.
- **Their error bodies are surfaced.** A bare "503" sends you debugging your own client;
  "503: Model search is temporarily overloaded" tells you to wait. Learned the hard way —
  their search was down while this was being built.

## VRAM planner (phase 5)

Both backends share one 12 GB card, and the documented failure mode is nasty: ComfyUI holds
VRAM after a job, KoboldCPP OOMs the instant it starts, and its `--rm` container disappears
before logging — so `stack up` says "did not come up" and `podman logs koboldcpp` says no
such container. It reads as KoboldCPP being broken when it is ComfyUI still holding 6.4 GB.

- **`ModelsApi.free_vram`** ("Release VRAM" on the dashboard's Morpheus card and the VRAM
  screen) is the fix for exactly that, and needs no restart.
- **Layer counts are read from the GGUF header** (`vram.gguf_meta`) — exact, not guessed.
  Worth knowing: Qwen3.5-9B has **32** layers while the built-in default is
  `KOBOLDCPP_GPU_LAYERS=33`, which is one more than exists. Kobold clamps, but it shows how
  arbitrary that default was.
- **The estimate is labelled an estimate.** VRAM for N offloaded layers is taken as N/total
  of the file plus a per-layer KV allowance; real usage depends on quant format, context and
  batch size. It exists to get you to a working number fast, not to be authoritative.
- **`DESKTOP_HEADROOM` (768 MB) is held back from every budget** — the KDE compositor is on
  this card too. Without it the planner produced "12.3 GB of 12.5 GB", which is
  arithmetically true and practically an OOM.
- **Live figures come from the host** (`services/gpu.py`, since 2026-11-19), so they show with
  ComfyUI stopped. AMD: amdgpu's sysfs counters, VRAM carve-out + GTT (on the Halo box models live
  in GTT, so the total is the sum, as in `koboldcpp/start.sh`), plus `gpu_busy_percent`. NVIDIA:
  the host's `nvidia-smi`, bind-mounted by `run-podman.sh`. ComfyUI's `/system_stats` is the fallback, and with
  none of the three the planner says so rather than inventing a total. The dashboard's GPU strip
  reads the same (`overview()["gpu"]`). "Release ComfyUI VRAM" is enabled only while ComfyUI
  answers (`vram.comfy_up()`).

**Per-model layer presets** live in `koboldcpp/model-presets.json` (gitignored, written
atomically). `model.env` only holds the *active* selection, so without this, switching
models loses the layer count you worked out for the previous one — and the right value
differs per quant, which is the whole reason it's worth remembering. Selecting a model with
no explicit layer count reuses its remembered one rather than silently dropping to the
default.

## Custom-node pins

`services/nodes.py` parses the `CUSTOM_NODES=(...)` array out of the `stack` script itself
and compares each pin to the checked-out HEAD. **The pins are read, never copied** — a
dashboard that disagreed with `stack doctor` about which commit is correct would be worse
than no dashboard.

Fixing drift is the `install_comfyui` verb (`stack install comfyui`), which is idempotent
and already knows how to clone, fetch and re-checkout. The target is baked into the argv
rather than accepted from a request: `install` also has `service` and `path` targets, and
those write under `$HOME` — which in this container is a throwaway directory, so exposing
the whole verb would offer two buttons that quietly do nothing useful.

- **`not_git` is excluded from the fix.** `stack install` reports a directory that isn't a
  git checkout and leaves it alone, so offering a button there would be a lie.
- **Expect a non-zero exit.** `stack install` runs its prerequisite check first, and
  the CDI spec check (host `nvidia-ctk`) cannot pass from inside this container, so the command exits
  1 *even when the node work succeeded*. The console appends a note saying so — otherwise a
  successful re-pin sits under the words "install failed".
- **Python deps are not covered.** A node arriving via a re-pin may still need a ComfyUI
  image rebuild, because deps live in `comfyui/Containerfile`. The failure mode — a node
  that imports and then errors at runtime — doesn't look like a missing dependency, so the
  panel says it in the header.

## Installing frontends (since 1.2.0)

The Frontends screen drives `stack frontend available|add|update|remove` (`ai-stack/CLAUDE.md`
→ *Official frontends*); `stack` does every real check, the dashboard only shows and asks.

- **What the browser can send:** `frontend_add` with a **name** that `stack frontend available
  --json` lists and reports not installed (target kind `catalog`), and `frontend_update` /
  `frontend_remove` with a repo `stack frontends --json` reports installed (kind `frontend`,
  official or not). Never a path. `stackctl.resolve` checks both against fresh `stack`
  output, so they follow the same allowlist rule as every other action. The exception is
  `frontend_add_url` with a git URL, from a browser on the local network only (*Security posture*).
- **From a git URL** (since 1.5.0): the third section of the screen. `frontend_catalog` returns
  `url_install` (this request is from the LAN); with it, a URL box and Install, confirmed in the
  danger style with the URL and a warning, then `frontend_add_url`. Without it, a note saying
  to open the dashboard by the host's address or local name, or to use `stack frontend add <url>`. The box is drawn once per mode
  (`_url_mode`), not on every poll, so typing survives the status refresh. `stack` shows what
  it cloned in the log; the dashboard has no preview step.
- **The list is read, not polled:** `StackApi.frontend_catalog` (→ `stackctl.catalog()`, not
  cached) when the screen mounts and whenever a job ends (`on_busy(False)`), since an install,
  update or remove changes it. A failure shows as a warning banner, never an error page.
- **Official vs Custom** comes from `stack`: `official` is true only when the installed clone's
  origin is the listed URL (`frontend_catalog json`), so a fork or a hand-made clone with the
  same repo name is labelled Custom. Frontends not in the list are always Custom.
- **Confirms:** every action asks first, naming what it does (the clone URL for Install). Remove
  uses wapyt's danger style (focus starts on Cancel), and the confirm text goes through
  `textContent`, so a brand from a `frontend.json` can't inject markup.
- **Available cards** show what the frontend uses (`needs`) and, when Install is disabled, why
  (`problems`: a port or container clash, a newer AI Ecosystem needed, a folder already there).
- **Newer release badge** (since 2026-11-20): `stack frontend check --json` (verb `frontend_check`,
  read-only: `git ls-remote` on each installed frontend's origin, nothing fetched), cached in
  `services/frontend_updates.py` like `release_check` (6 h, 30 min after a failure, off with
  `STACK_UPDATE_CHECK=off`) and served by `StackApi.frontend_updates`. The app asks at load
  (`check_frontend_updates`), keeps `app.frontend_updates` (repo → row), puts the count of
  newer ones on the sidebar's *Installed apps* entry, and asks again with `force` after any
  `frontend_*` job. A card whose row has `newer` gets a green "vX.Y.Z available" tag and a
  labelled green Update button. Git in the container has no login of its own: a private
  frontend repo on GitHub is reached with `GITHUB_TOKEN` (tokens.env, which `stack` offers git
  as a credential helper, since 1.5.0); without one it reads as `unreachable` and shows nothing.
- `STACK_CATALOG` is passed through to `stack` (tests only; unset in the image).
- Tests: `tests/test_frontend_actions.py` (what `resolve` accepts), and end to end with the dev
  server on a scratch `AI_ECOSYSTEM` and a test list: install, Official vs Custom, remove refused
  while the folder holds data, clean remove, 0 console errors.

## About and the release check (since 2026-11-20)

Copied from IguanaXterm's (`El-Iguana/iguanaxterm_wapyt`: `about.py`, `release_check.py`, the
About dialog and toolbar badge), adapted:

- **The version isn't in `about.py`.** It is `ai-stack/VERSION`, which the page gets from
  `release` in the overview and the server reads in `release_check.current_version()`, so About,
  `stack version` and the toolbar can't disagree. A cached answer is dropped when that version
  changes (after an update the old "newer" must not stick).
- **A private repo or fork** (ai-stack itself was private until it went public): GitHub answers
  404 for its releases without a token. `GITHUB_TOKEN` (tokens.env, passed in by `run-podman.sh` like `HF_TOKEN`) is sent as a
  bearer header, only to api.github.com; read-only *Contents* on the repo is enough. Failures are
  told apart for the screen: `private` (404, no token), `token` (sent one, refused), `unreachable`.
  `stack version --check` exists so nobody *needs* a token: it uses git and the user's login.
- **Plain module for the cache** (`release_check`), BFF method on `StackApi`; 6 h cache, 30 min
  after a failure, "Check again" forces one. One request per server, not per tab.
- **An About screen, not a dialog**, because everything else here is a routed screen
  (`#/about`). The toolbar's **Update** button (wapyt `accent`, hidden) appears only when a newer
  release is out and opens it.
- `about.py` is imported by the browser: keep it pure (no file or network access) and import
  *names* from it.

## What the backends have loaded (`services/backends.py`)

The dashboard's top section. The two backends answer differently, and the cards keep that
difference visible:

- **KoboldCPP names its model** (`/api/v1/model`), so "Loaded" is a fact, shown with size,
  architecture, layer count (from the GGUF header) and context (`/api/extra/true_max_context_length`).
  The selection in `model.env` is shown beside it only when it differs or KoboldCPP is down.
  The "N of M layers on GPU" figure is hidden while a restart is pending: `model.env`'s layer
  count then describes the *selected* model, not the loaded one.
- **ComfyUI cannot name its resident models** — there is no endpoint for it; it loads per
  job and keeps what it can. The card says how much VRAM torch holds (`/system_stats`
  `torch_vram_total`; under 256 MB reads as "no models resident", a warm CUDA context) and
  which files the running job (`/queue`) or the last one (`/history?max_items=1`) named. It
  never says "X is loaded". Files are found by **extension of any input value**, not by a
  list of loader node classes, so custom-node loaders work without a table to maintain.
- Every probe is best-effort with a 2 s timeout: a backend that is down is a normal state,
  not an error. Tested against fake servers in `tests/test_backends.py` (the real ones need
  the GPU).
