# Changelog

AI Ecosystem (repo: `ai-stack`) releases. The version is in [`VERSION`](VERSION) and follows
[Semantic Versioning](https://semver.org/):

- **Major**: a change that breaks something outside this repo: the `frontend.json` format, the
  shape of `stack status --json`, a backend's name or port on `ai-net`, or a command removed or
  renamed.
- **Minor**: a new feature: a backend, a command, a dashboard screen.
- **Patch**: a fix.

`stack version` prints the release and the checkout's commit, and the dashboard shows it beside
its name. Each release is tagged `v<version>` on `master`, has a GitHub release with its
section from this file, and republishes the wiki (steps in `CLAUDE.md` → *Versioning and
releases*).

## Unreleased

## 1.1.0 — 2026-11-20

### Added
- An **About** screen in the dashboard: the release you're on, whether a newer one is out, how
  to update, and links to the wiki, source, releases, changelog and issues. An **Update** badge
  appears in the toolbar when a newer release is out. The repo is private, so the check needs a
  `GITHUB_TOKEN` in `stack-ui/tokens.env`; `STACK_UPDATE_CHECK=off` turns it off.
- `stack version --check`: is a newer release out? Uses git and your own login, no token.

### Fixed
- On a narrow window the dashboard's toolbar no longer clips its name, version and state.

## 1.0.0 — 2026-11-20

The first numbered release, of the stack as it is in daily use. Earlier history is in the git
log and the merged pull requests (#1–#21).

### Backends, all behind one router
- **Text**: KoboldCPP, with the chat format picked from the model file, a context window chosen
  in the dashboard, and model swaps that wait for VRAM instead of crashing.
- **Image and sound**: ComfyUI, built from a pinned recipe, with pinned custom nodes (PuLID-FLUX
  for Iguana Studio's Person mode).
- **Decisions**: Fortuna, typed yes/no, choice and score answers on `http://fortuna:8009`, its
  models pinned by sha256 and chosen in the dashboard.
- The **router** owns `koboldcpp`, `comfyui` and `fortuna` on `ai-net`, so text can move to another
  machine with `stack route` and nothing else changes.
- Runs on the NVIDIA workstation and on the AMD Ryzen AI Halo box (`host.env` host profile).

### The `stack` command
- `stack on` / `stack off` start and stop the backends now; `stack boot on|off` sets start at
  boot separately.
- Frontends are discovered from each repo's `frontend.json`; `stack frontends` lists them.
- `stack install` sets everything up and is safe to re-run. It rebuilds the dashboard when its
  code has changed.
- `stack status --json`, `stack doctor` and `stack version`.

### The AI Ecosystem dashboard
- An Infrastructure workspace (the router, one card per capability, the GPU) and a Frontends
  workspace for the installed apps, with HTTPS links where an app offers them.
- Models: installed GGUFs, ComfyUI files and Fortuna's models; HuggingFace and Civitai search
  and downloads, with where each file came from.
- VRAM planner, logs, custom-node pins, doctor.
- Six themes, open to the whole LAN.

### Documentation
- The wiki in `docs/wiki/`: a user guide and a developer guide.
