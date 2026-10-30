# stack-ui — the AI Ecosystem control dashboard

Web frontend for the stack itself. **Phases 0–5 built:** service status and control, streamed
logs, model inventory with selection and delete, HuggingFace + Civitai browsing and downloads,
and a VRAM planner across both backends.

**pytincture + wapyt since 2026-10-26.** The page is Python in the browser (Pyodide) built
from wapyt widgets — the same stack as the Pytinc apps (`~/Development/Pytinc/basilisk_wapyt`
is the closest sibling; its and Monguana's CLAUDE.md files document the framework traps).
The vanilla-JS page and the JSON API it called are gone; `services/` carried over unchanged
apart from moving into `appcode/`. Layout: a header toolbar (the switch and whole-stack
actions), a sidebar of screens, and a **dashboard home** — backends and what they have
*loaded* on top, frontends below. Language models (installed GGUFs + HuggingFace search),
image models (ComfyUI files + Civitai search), VRAM planner, logs, custom nodes and doctor
are sidebar screens. Screens route by URL hash (`#/llms`), so a reload keeps your place.

Full roadmap: [`../STACK_UI_PLAN.md`](../STACK_UI_PLAN.md). This lives *inside* the ai-stack
repo — it's part of the stack, not a consumer of it, so `stack install` builds it and `stack`
reaches it via `$SELF` like the comfyui recipe.

**Naming.** The facing name is **AI Ecosystem** — the browser tab title, the toolbar brand,
the `stack status` description, and the startup banner all say that. Everything an
operator *types* is unchanged and must stay unchanged: the `stackui` service key (`stack stackui`,
`stack down stackui`, `stack install stackui`), the `localhost/stackui` image, this `stack-ui/`
directory, and the pytincture application name `stackui` (the page is `/stackui/`; `/`
redirects there). Rename labels, never identifiers.

## A container that drives the host

Every other app here is a container, and so is this one — but it controls the host, so
three mounts are load-bearing (all in `run-podman.sh`):

| Mount / flag | Why |
|---|---|
| `podman.sock` | `stack` drives podman; this is the host engine. The image ships the **podman client only** (static `podman-remote`, pinned to the host version) — this container must never run containers itself. |
| session bus (`$XDG_RUNTIME_DIR/bus`) | `stack on`/`off` are `systemctl --user`. Without it the switch can't work. |
| `--network=host` | `stack` health-probes `http://localhost:5001`. In its own netns those are the *container's* ports and everything reads down. It also makes uvicorn's `127.0.0.1` bind the host loopback, so no port is published. |
| `--userns=keep-id` | The session bus authenticates EXTERNAL against the uid the **host** sees. Under the default rootless mapping the process is uid 0 inside and 1000 outside, they disagree, and the bus answers `REJECTED`. |
| `--device nvidia.com/gpu=all` | Injects the **host's** `nvidia-smi` via the CDI spec (it must match the host driver, so it isn't installed in the image). `koboldcpp/start.sh` reads free VRAM with it to wait out a model swap; without it those waits silently skip. Creates no CUDA context, so no VRAM cost. Skipped with a warning if the CDI spec is missing. |

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

Then http://localhost:7863 (redirects to `/stackui/`). `stack status` lists it under **Control**.
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
service.py             # ASGI entry: create_app + the GET event streams; REFUSES a non-loopback bind
Containerfile          # python 3.13 + wapyt wheels + podman CLIENT + the systemctl shim
run-podman.sh          # the run flags, each one load-bearing — read the header
shim/systemctl         # systemctl -> D-Bus translation (see above)
tests/                 # test_backends.py (pytest), smoke.py (Playwright)
appcode/               # pytincture modules_path
  stackui.py           # the page: toolbar, sidebar, routing, action console, download strip
  ui/                  # browser-only screens (pytincture ships what the page imports)
    common.py          #   spawn, esc, toast, failed(), Stream (EventSource), Listeners
    dashboard.py       #   home: backend cards (loaded models) + frontend cards
    llms.py            #   Language models: Installed + Find on HuggingFace
    images.py          #   Image models: Installed + Find on Civitai + Find on HuggingFace
    vramview.py        #   VRAM planner
    system.py          #   Logs, Custom nodes, Doctor
  static/stackui.css   # served at /static (no-cache, so a rebuild's CSS shows on next load)
  wapyt-99.99.99-*.whl # the BROWSER's wapyt (git-ignored; dev_wheel.sh / the Containerfile)
appcode/services/
  stack_api.py         # BFF: overview, action, current_job, doctor, nodes
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

- **Loopback only.** `service.py` exits rather than bind a non-loopback address. There's no
  auth, and that's only tolerable while it can't be reached off-box. (Companion binds
  `0.0.0.0`; that pattern must not be copied here.) If remote access is ever wanted, it
  needs auth in front first — companion's `services/auth.py` drops in.
- **Host + Origin guards on every write.** Without auth the browser is a confused deputy:
  any page you have open can POST to localhost. Since the pytincture port these come from
  pytincture itself rather than our middleware: `allowed_hosts` (127.0.0.1/localhost/::1)
  answers any other Host with 400, which blocks DNS rebinding; a BFF write must be
  `application/json` (a cross-origin form can't send that, and anything that can is forced
  through a CORS preflight nobody answers) and must carry a same-origin `Origin` /
  `Sec-Fetch-Site`. That replaces the old `X-Stack-UI: 1` header, which gave the same
  guarantee. Verified 2026-10-26: cross-origin JSON → 403 "Origin validation failed", a
  form post → 415. The event-stream routes are GET-only and read-only. Not a substitute for
  auth — it's what makes loopback-only defensible.
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
- **The doctor is not polled.** It shells out to podman several times; it's on-demand
  behind a button.
- **`STACK_GPU` must be in `stackctl._PASSTHROUGH` too** (2026-10-30). `run-podman.sh` resolves
  the host profile (`lib/host.sh`) on the host and passes `-e STACK_GPU`, because the start
  scripts detect the GPU with `nvidia-ctk` / `/dev/kfd`, which this container doesn't have. Drop
  it and a KoboldCPP restarted from the dashboard comes up **with no GPU**, without an error.
- **`CONTAINER_HOST` and `DBUS_SESSION_BUS_ADDRESS` must be in `stackctl._PASSTHROUGH`.**
  The subprocess env is a deliberate allowlist, and dropping either fails *quietly*:
  without the first, podman talks to an engine that isn't there and every up/down does
  nothing; without the second, the switch silently disappears from `stack status`.
- **Mount the podman socket's directory, never the socket file.** A file bind-mount pins the
  inode; when `podman.socket` re-creates the socket, the container keeps the dead one and every
  action fails with `dial unix /run/podman/podman.sock: connection refused` while the page
  itself still loads (status is read by health probes, not podman). Hit on 2026-09-23.
- **The ecosystem is mounted at its REAL host path, never a tidy `/eco`.** With a remote
  client, `-v` and build-context paths are resolved by the *host* engine. A path that
  exists only inside this container produces containers with broken mounts — and it fails
  quietly: `stack iguana` returned exit 0 while starting nothing usable. Identical paths on
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
- **Live figures need ComfyUI running.** No NVIDIA tooling in this container; the planner
  degrades to "start ComfyUI for live figures" rather than inventing a total.

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
