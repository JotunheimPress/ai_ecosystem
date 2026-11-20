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

### Changed

- World Builder's description in the official list no longer names Godot: "VR scene generation
  from a prompt".

## 1.5.0 — 2026-11-20

### Added

- World Builder (beta) joins the official frontends: VR scene generation from a prompt.
  `stack frontend add world-builder`, or Install on the dashboard, gets v0.1.0 (port 7880,
  uses text and decisions).
- `stack frontend add <git URL>` installs any frontend, not just official ones: its newest
  release, checked against the stack and your installed frontends, then built. It shows what
  it is and asks first, since nobody reviewed it (`--yes` to skip).
- The dashboard's Frontends screen installs one from a git URL too, under **From a git URL**,
  from any computer on your local network (the dashboard opened by the stack machine's
  address or local name). Requests from outside the network, or made by another website, are
  refused: the dashboard has no login, and the install runs code nobody reviewed.
- Private frontend repos on GitHub: `GITHUB_TOKEN` in `stack-ui/tokens.env` is used by
  `stack frontend add`, `update` and `check` after your own git login, so the dashboard's
  "vX.Y.Z available" check now reaches them too.

### Fixed

- A frontend cloned over ssh (`git@github.com:owner/repo`) from an official repo listed by its
  https URL is now labelled Official, not Custom.

## 1.4.0 — 2026-11-20

### Added

- A frontend with a newer release on its origin says so: "vX.Y.Z available" on its card on the
  dashboard's Frontends screen, with a green Update button, and a count beside *Installed apps*
  in the sidebar. From a terminal, `stack frontend check`. Checked every 6 hours and after any
  install, update or remove; `STACK_UPDATE_CHECK=off` turns it off.

## 1.3.0 — 2026-11-20

### Added

- Iguana Studio is the first official frontend: `stack frontend add iguana_studio`, or Install
  on the dashboard's Frontends screen, gets its newest release (port 7862, uses image-sound).

## 1.2.1 — 2026-11-20

### Fixed

- Frontend cards: the Official/Custom label is no longer cut off on long names, Update and Remove
  stay together when the buttons wrap, and the empty Available note uses the full width.

## 1.2.0 — 2026-11-20

### Upgrading

The repo moved to `JotunheimPress/ai_ecosystem`, with a new history. An existing clone keeps
pulling the old one until it is pointed at the new one (this discards local commits on `master`;
keep anything you need on a branch first):

```bash
cd ai-stack
git remote set-url origin https://github.com/JotunheimPress/ai_ecosystem.git
git fetch --prune --tags --force origin && git reset --hard origin/master
stack install
```

### Added

- MIT license.
- Official frontends: `frontends.json` lists them, `stack frontend available` shows them, and
  `stack frontend add NAME` installs one (clones its latest release, checks it against the
  list, builds it). `stack frontend update` and `stack frontend remove` work on any installed
  frontend; `remove` refuses while the folder holds data git doesn't have. The list is empty
  until frontends go public.
- `frontend.json` may name a `build` script, run by `stack frontend add` and `update`.
- The dashboard installs official frontends: the Frontends screen lists those not installed under
  **Available**, with an **Install** button (or the reason it can't be installed), and every
  installed frontend gets **Update** and **Remove** buttons and an **Official** or **Custom** label.

### Changed

- The repo is `JotunheimPress/ai_ecosystem` on GitHub (it was `ai-stack`). Clone it as
  `ai-stack/` as before; the dashboard now works whatever the checkout's folder is called.
- The README and wiki are written for anyone installing from scratch: frontends are described in
  general, not as a list of private repos, and hardware is given as what it was tested on.

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
