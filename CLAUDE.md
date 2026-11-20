# AI Ecosystem (repo: ai-stack) — unified AI backend + frontend launcher

**Branding.** The product is **AI Ecosystem**: the README title, the wiki, the dashboard (its
browser tab and toolbar), `stack version` and the changelog all say that. What an operator
*types* stays as it is: the repo `ai-stack`, the command `stack`, the `stackui` service key, the
`localhost/stackui` image, the `stack-ui/` directory. Rename labels, never identifiers (same
rule as `stack-ui/CLAUDE.md` → *Naming*, and the repo-is-identity rule for frontends).

One `stack` command to run the **shared AI backends** and then **pick a frontend**. Backends are
shared across every app; frontends run side-by-side on their own ports.

**Wiki:** `docs/wiki/` (user guide + developer guide, linked Markdown) is the source, reviewed in
PRs with the code. `docs/publish-wiki.sh` publishes it to the GitHub wiki
(`github.com/JotunheimPress/ai-stack/wiki`, enabled 2026-11-20): pages are named from their
titles (`README.md` → Home), links out of `docs/wiki/` become GitHub links on master, and it adds
a sidebar and a footer naming the release and commit. Edits made on GitHub are overwritten by the
next publish. It publishes only committed pages; `--dry-run` shows the change, `--out DIR` just
builds. Run it after a PR that touches `docs/wiki/` merges. The wiki summarises and links to
these CLAUDE.md files, which stay the source of truth; update a wiki page when behaviour a user
sees changes.

## Structure

- `stack` — the launcher (bash). Backends and stackui are a block of `reg` rows near the top;
  **frontends are discovered**: any repo directly under `$ECO` with a `frontend.json` (see
  *Frontends* below). Symlinked onto PATH at `~/.local/bin/stack`.
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
  `GPU=nvidia|amd`, `LLM_BACKEND=cuda|vulkan|rocm`, `BIND`, `COMFYUI_PORT` (host port only,
  default 8188; the Halo uses 8189 because AMD Rex runs its own socket-activated ComfyUI on
  127.0.0.1:8188 — on ai-net it is always `comfyui:8188`). Sourced by `stack` and every backend
  script; it supplies the GPU flags, image tags and published-port address. Without `host.env`
  the GPU is detected — NVIDIA CDI first, since this workstation also has `/dev/kfd` from its
  CPU's graphics — and the 4070 setup is byte-for-byte what it was. The AMD Halo box is the
  other profile (`HALO_PLAN.md`); both stay supported.
- `router/` — the **backend router**: one nginx container (`backend-router`, stock
  `nginx:1.28-alpine` + `router.conf.template`) that owns the name `koboldcpp` on ai-net and host
  port **5001**, and forwards it to where text runs. The local copy runs as `koboldcpp-local` with
  no host port. Route state is `$ECO/router/route.conf`, written only by `stack route`. Groundwork
  for the Halo box (`HALO_PLAN.md`). See `router/CLAUDE.md`.
- `fortuna/` — **Fortuna**, typed decisions (yes/no, choice, score as calibrated probabilities)
  in TypeSafe's `/v1/systemone` shape, for frontends that today ask KoboldCPP for a label and parse
  it. Engine: JevK5 (a GGUF) on the official llama.cpp server image, pinned by digest. Named
  `fortuna` everywhere (not after its engine) so the engine can be swapped. State in
  `$ECO/fortuna/` (models/, fortuna.env). See `fortuna/CLAUDE.md`.
- `comfyui/` — the **canonical** ComfyUI recipe, same shape as `koboldcpp/`: `Containerfile`
  (ubuntu:24.04 + cu130 torch + ComfyUI cloned at a pinned tag), `start.sh`, `.containerignore`,
  `INSTALL.md`, `CLAUDE.md`, and two lock files — `requirements.lock.v0.31.1.txt` (current, COPYed
  into the build) and `requirements.lock.pre-0.31.txt` (rollback artifact). They live here rather
  than in `~/Development/ai_ecosystem/ComfyUI/`, which is an upstream checkout where our files would
  be untracked and lost on update. That checkout now supplies **only mutable state** (`models`,
  `input`, `output`, `user`, `custom_nodes`), bind-mounted at `/data` via `--base-directory`.

## Usage

```bash
stack on / stack off  # start / stop the backends NOW (= stack up / stack down backends)
stack boot [on|off]   # show / set START AT BOOT — a separate setting, starts/stops nothing now
                      #   gaming: stack off (+ stack boot off to stay off after a reboot)
stack              # interactive: show status, offer to start backends, then pick a frontend
stack up           # start backends now (never changes start at boot)
                   #   (skips the local text model while text is routed to Halo)
stack status       # what's up (+ whether the backends start at boot)
stack status --json  # the same for stack-ui and scripts (shape: status_json in `stack`)
stack companion    # start one app: a backend, stackui, or a frontend by repo name
stack frontends    # installed frontends (--json for stack-ui)
stack down         # stop everything   (also: down backends | down frontends | down <app>)
stack restart <app>  # restart router = replace it in place, no stop first
stack logs <app>   # follow logs (podman logs -f — every app is a container)
stack route [local|halo]  # show / set where text is served (halo needs HALO_HOST in halo.env)
stack fortuna-model [list|fetch NAME|verify NAME]  # Fortuna's pinned model files
                   #   (fortuna/models.table): sha256-checked download, or re-hash one

stack install      # one-time setup, idempotent: build backend images, create ai-net,
                   #   install the systemd unit, link onto PATH.
                   #   targets: all|backends|router|koboldcpp|comfyui|fortuna|stackui|service|path
stack doctor       # check prerequisites + what's installed; changes nothing
```

Typical: `stack up` (backends) → `stack` and pick a frontend, or `stack companion` directly.

## Registry (apps it manages)

| App        | Kind      | Port | Backend it needs | How it starts |
|------------|-----------|------|------------------|---------------|
| router     | container | 5001, `HOST_COMFY_PORT`, 127.0.0.1:8009 | — | `ai-stack/router/start.sh` — container `backend-router`, aliases `koboldcpp`, `comfyui`, `fortuna`; started first, since every backend is health-probed through it |
| koboldcpp  | container | (via router) | —        | `~/Development/ai_ecosystem/koboldcpp/start.sh` (detached, on `ai-net`, container `koboldcpp-local`) |
| comfyui    | container | (via router) | —        | `ai-stack/comfyui/start.sh` (detached, on `ai-net`, container `comfyui-local`) |
| fortuna    | container | (via router) | — | `ai-stack/fortuna/start.sh` (container `fortuna-local`) — typed decisions, `http://fortuna:8009/v1/systemone` on ai-net; models from `fortuna/models.table`, fetched only by `stack fortuna-model fetch` and sha256-verified; `stack doctor` reports the configured one (`fortuna/CLAUDE.md`) |
| *frontends* | container | from `frontend.json` | varies | whatever is installed — see below. Today: `companion` 7861, `ai-story-maker` 7860, `iguana_studio` 7862, `nocturne` 7864 |

## Topology

- **Shared network `ai-net`** (renamed from the legacy `sillytavern-net`). Containerized apps join it
  and reach every capability by name, **all through the backend router** (since 2026-11-19):
  `http://koboldcpp:5001` (text), `http://comfyui:8188` (image and sound), `http://fortuna:8009`
  (decision). The router forwards to `koboldcpp-local` (or the Halo box, `stack route`),
  `comfyui-local` and `fortuna-local`, none of which publish a host port.
- **From the host:** `localhost:5001`, `localhost:$HOST_COMFY_PORT` (8188, or 8189 where the OS runs
  its own ComfyUI on 8188) and `127.0.0.1:8009`, all published by the router.
- **Anything about "the copy on this GPU" bypasses the router** with `podman exec` into the
  `*-local` container: `stack status`'s local probes, Fortuna's start wait, KoboldCPP's request
  that ComfyUI free its memory.
- Frontends don't conflict — distinct ports (7861/7860/7862), so several can run at once. "Pick a
  frontend" is about convenience, not exclusivity.

## Frontends (`frontend.json`)

Frontends are not registered in `stack`. A frontend is any repo directly under `$ECO` with a
`frontend.json` at its root, so the stack — and stack-ui — list exactly what's installed. Install
one by cloning it next to ai-stack; remove it by removing the clone.

```json
{
  "repo": "companion",                          // identity: what you type, = the repo name
  "brand": "companion",                         // what a UI shows (defaults to repo)
  "description": "Character-card AI companion",
  "port": 7861,
  "health": "http://127.0.0.1:7861/api/health", // must be on localhost / 127.0.0.1
  "container": "companion",                     // podman name, for stop + logs
  "start": "run-podman.sh",                     // inside the repo, executable
  "icon": "mdi-account-heart-outline",          // optional, stack-ui card icon
  "https_port": 7868,                           // optional: same app over HTTPS (mic from the LAN)
  "https_setup": "/ca"                          // optional: page on the HTTP port for trusting its cert
}
```

- **The repo name is the identity** (`stack companion`, `stack ai-story-maker`, `stack iguana_studio`);
  the brand is display only. There are no short aliases — `story` / `iguana` were dropped on
  2026-11-07 when this replaced the hardcoded rows.
- **A fork must change `repo`, `port` and `container`** (e.g. npc_maker forked from companion). Two
  manifests with the same `repo` → the second is skipped; a shared port or container name is
  allowed but `stack doctor` warns, since the two can't run at once. A `repo` that differs from the
  clone's git origin also warns.
- Bad manifests are skipped and reported by `stack doctor` / `stack frontends`, never fatal. A
  `repo` that collides with a stack verb or built-in app (`status`, `koboldcpp`, …) is refused.
- Parsed by `frontend_manifests` in `stack` with **python3** (present in the stack-ui image; jq
  isn't). stack-ui gets brands and icons from `stack frontends --json` rather than reading the
  manifests itself, so the two can't disagree.
- **No desktop avatar** (removed 2026-11-07). `stack desktop`, `stack install desktop` and the
  dashboard's Desktop avatar button are gone. ai-stack is the backends plus frontend discovery, and
  nothing frontend-specific belongs here. Cantinero (companion-assistant) runs its desktop shell on its
  own (its `desktop/README.md`). A unit left over from an old install at
  `~/.config/systemd/user/pantheon-desktop.service` is no longer managed by ai-stack.

## Install (`stack install`)

Executable form of `koboldcpp/INSTALL.md` + `comfyui/INSTALL.md`, so the docs and the real thing
can't drift. Every step is idempotent — re-running reports what's already in place and does nothing.

What it does: checks prerequisites (podman, host driver, CDI spec) → links `~/.local/bin/stack` →
renders the systemd unit and `daemon-reload`s → creates `ai-net` → links the KoboldCPP recipe into
`$ECO/koboldcpp` → builds the backend images (KoboldCPP, ComfyUI, Fortuna) →
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

The dashboard image is the exception to "already in place means done": its code is baked in, so
install rebuilds it when `stack-ui/` has changed since it was built and restarts a running
dashboard on it (`stack-ui/CLAUDE.md` → *Stale images are rebuilt*).

It also pulls the router's stock nginx image, so the first `stack up` (at boot, maybe offline)
doesn't have to.

`stack doctor` is the read-only half — prerequisites, images, network, unit, plus `stack status`.
The unit check compares the whole installed file with what `stack install service` would write,
so an old unit (wrong path, or the old 240 s start timeout) is reported.

## Versioning and releases (since 1.0.0, 2026-11-20)

- **`VERSION`** holds the release, SemVer. **Major**: something outside this repo breaks (the
  `frontend.json` format, the `stack status --json` shape, a backend's name or port on `ai-net`,
  a command removed or renamed). **Minor**: a feature. **Patch**: a fix.
- **`CHANGELOG.md`**: a PR that users would notice adds a line under *Unreleased*.
- **Shown by** `stack version` (`-V`, `--version`), the first line of `stack doctor`, and the
  dashboard beside its name, with the commit in the tooltip. All read `VERSION` through `stack`
  (`STACK_VERSION`, `stack_release`); `stack status --json` carries it as `release`
  (`{version, commit, modified}`). Its `"version": 1` is the JSON *shape's* version, not the
  release; don't confuse the two. The commit is omitted where git isn't available (the
  stack-ui image).
- **Cutting a release**, in order:
  1. A PR that bumps `VERSION` and moves *Unreleased* under a new `## X.Y.Z — date` heading.
  2. After it merges, on an up-to-date `master`: `git tag -a vX.Y.Z -m "AI Ecosystem X.Y.Z"`
     and `git push origin vX.Y.Z`.
  3. The GitHub release, with that changelog section as its notes:
     `gh release create vX.Y.Z --repo JotunheimPress/ai-stack --verify-tag
     --title "AI Ecosystem X.Y.Z" --notes-file …`.
  4. Publish the wiki: `docs/publish-wiki.sh` (`--dry-run` first). Every release does this,
     even with no page changed, so the wiki's footer names the new release. Between releases,
     run it after any merged PR that touches `docs/wiki/`.

## Running now vs. start at boot (two separate settings)

Until 2026-11-19 one switch did both: `stack off` stopped the backends **and** disabled boot. They
are now independent:

- **What runs now:** `stack on` / `stack up` start the backends, `stack off` / `stack down backends`
  stop them (frontends keep running). None of them touch boot. The dashboard: *Start backends* /
  *Stop backends*, next to a "Backends running / stopped / N of M up" label.
- **Start at boot:** `stack boot on|off` enables or disables `ai-stack-backends.service`, a
  **systemd user** unit (oneshot, `ExecStart=stack up`, `ExecStop=stack down backends`). Plain
  `enable`/`disable`, never `--now`, so it starts or stops nothing now. The dashboard: the
  *Start at boot* toggle, saved at once. `stack enable` / `stack disable` are aliases.
- **Gaming:** `stack off` frees the GPU now; add `stack boot off` if it should stay free after a
  reboot. `stack gaming` is an alias for `stack off` only.
- An enabled unit stays "active (exited)" after boot whatever `stack off` does later. Harmless: its
  ExecStop at shutdown just stops whatever is still running. `stack status --json` reports the
  setting as `boot` (and as `switch`, its old name, for older readers).

The shipped unit is a **template**: `@SELF@` is substituted with the checkout's own path at
install time, so it works wherever ai-stack is cloned. Install it with `stack install service`, not
`cp` — a raw copy has an unsubstituted placeholder and systemd rejects it (loudly, which is the
point; the old hardcoded `~/Development/ai_ecosystem/...` path failed *silently* on any other layout).

```bash
stack install service         # render the unit + daemon-reload (COPY, never a symlink —
                              #   systemctl disable deletes a symlinked unit, breaking `stack boot on`)
loginctl enable-linger        # start at boot even before you log in
stack boot on                 # start at every boot (and `stack on` to start now)

systemctl --user status ai-stack-backends   # low-level check
journalctl --user -u ai-stack-backends -f   # boot logs
```

Reuses the stack's own start logic (DRY), verified under the systemd `--user` context. Frontends stay manual — start at boot only governs the shared backends.

## `stack status --json`

What stack-ui reads instead of scraping the text. Only JSON goes to stdout; the shape is documented
above `status_json` in `stack`. Per app: `group` (backend/frontend/control), `label`, `capability`
(from the `CAP` table: routing, text, image-sound, decision), `port`, `up`. Backends also carry
`local`: the state of **this machine's copy** (`up`, `loading`, `stopped`, `absent`, or `down`).

- `up` is the **served** state, through the router where there is one. With text routed to Halo,
  `koboldcpp.up` is the Halo box.
- `local` is probed (`podman exec <container> sh -c "$LOCAL_PROBE"`) only when served can't stand
  for it: the app is routed elsewhere, or the router is down. Otherwise `probed: false` and the state
  is inferred. Not podman's health field, which reads "starting" for days on `koboldcpp-local`.
- `route.router_config` is `stale` when the running router was created from an older template,
  image or port set (`router/start.sh --check`); the next `stack up` recreates it.
- Health probes run in parallel, so status costs one timeout (3 s) however many apps are down.

## Notes / gotchas

- **`stack up` exits non-zero if any backend failed**, after trying all of them, and names them.
  Backends stop in reverse order (router last), so text answers until the model behind it is gone.

- `koboldcpp/start.sh` runs the container **detached** (`podman run -d`) but returns only once the
  model has loaded (or failed): it waits for the old container's VRAM, asks ComfyUI to unload its
  cache, retries in CUDA unified memory when VRAM is merely fragmented, and on a
  real shortage retries with fewer layers — see `koboldcpp/CLAUDE.md`. It picks the chat format
  (adapter) from the model file: ChatML for Qwen, `mistral-nemo.json` for Rocinante, AutoGuess
  otherwise. A swap takes ~25 s; a fallback ~35–50 s.
- **Both backends run with `--restart=on-failure:5`, not `--rm`.** Before this, a crashed backend
  vanished without a trace while boot auto-start still said ON (found 2026-09-24: both gone, no container,
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
  `http://koboldcpp:5001`; that name is the router's. `host.containers.internal:<HOST_COMFY_PORT>`
  still works (the router publishes it), but it's no longer the default anywhere.
- The systemd unit must be a **copy** in `~/.config/systemd/user/`, not a symlink — `systemctl disable`
  removes symlinked units (which then breaks `stack boot on`). `install_service` renders it from the
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
