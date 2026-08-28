# stack-ui — the AI Ecosystem control dashboard

Web frontend for the stack itself. **Phases 0–5 built:** service status and control, streamed
logs, model inventory with selection and delete, HuggingFace + Civitai browsing and downloads,
and a VRAM planner across both backends.

Full roadmap: [`../STACK_UI_PLAN.md`](../STACK_UI_PLAN.md). This lives *inside* the ai-stack
repo — it's part of the stack, not a consumer of it, so `stack install` builds it and `stack`
reaches it via `$SELF` like the comfyui recipe.

**Naming.** The facing name is **AI Ecosystem** — the page title, the `<h1>`, the FastAPI app
title, the `stack status` description, and the startup banner all say that. Everything an
operator *types* is unchanged and must stay unchanged: the `stackui` service key (`stack stackui`,
`stack down stackui`, `stack install stackui`), the `localhost/stackui` image, this `stack-ui/`
directory, and the `X-Stack-UI: 1` write header. Rename labels, never identifiers.

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
  inventory.py         # models on disk, selection, delete, per-model layer presets
  hub.py               # HuggingFace search + fit hints
  civitai.py           # Civitai search (checkpoints / LoRAs / VAEs)
  downloads.py         # transfers: progress, resume, host allowlist
  provenance.py        # where each downloaded model came from (model-sources.json)
  vram.py              # GGUF header parsing, live VRAM, the planner
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
- **Every result links out to its own page** (`pageLink()` in `app.js`): the repo on
  huggingface.co, the model — and each *version* — on civitai.com. The result rows show
  sizes and quants, never the model card, so the licence, the prompt format and the
  example images are one click away instead of unreachable. The link lives inside the
  `<summary>`, so its click handler calls `stopPropagation()` — otherwise opening the
  page would also toggle the row.
- `HF_TOKEN` is picked up from the environment for gated repos.

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
- The store is a cache of provenance, not a source of truth about what exists: missing,
  unreadable or corrupt degrades to "no entry" and never blocks a listing, and a failed
  write never turns a finished 7 GB download into an error.

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

- **`POST /api/vram/free`** is the fix for exactly that, and needs no restart.
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
  `nvidia-smi` + the CDI spec cannot pass from inside this container, so the command exits
  1 *even when the node work succeeded*. The console appends a note saying so — otherwise a
  successful re-pin sits under the words "install failed".
- **Python deps are not covered.** A node arriving via a re-pin may still need a ComfyUI
  image rebuild, because deps live in `comfyui/Containerfile`. The failure mode — a node
  that imports and then errors at runtime — doesn't look like a missing dependency, so the
  panel says it in the header.
