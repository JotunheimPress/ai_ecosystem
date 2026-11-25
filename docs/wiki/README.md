# AI Ecosystem wiki

**AI Ecosystem** runs a local, self-hosted AI stack on one machine: shared GPU **backends**
(text, image and sound, decisions) behind one **router**, the **frontends** that use them, and a
dashboard for all of it. One command, `stack`, drives everything. AI Ecosystem is the brand; the
repo is `ai-stack` and the command is `stack`. Releases: [`VERSION`](../../VERSION),
[changelog](../../CHANGELOG.md), `stack version`.

This wiki has two halves. The **user guide** is for running the stack. The **developer guide**
explains how it is built and points into the detailed notes (`CLAUDE.md` files) that sit next to
the code.

## User guide

| Page | What it covers |
|---|---|
| [Getting started](getting-started.md) | Requirements, install, the first start, where models go |
| [Running the stack](running-the-stack.md) | Start and stop, start at boot, status, gaming, logs |
| [The dashboard](dashboard.md) | AI Ecosystem: workspaces, screens, actions, themes, LAN access |
| [Models](models.md) | Text (KoboldCPP), image and sound (ComfyUI), decisions (Fortuna); downloads, verification, the VRAM planner |
| [Frontends](frontends.md) | The apps that use the backends: installing, starting, ports, HTTPS |
| [Text on another machine](text-routing.md) | Routing text to the Halo box with `stack route` |
| [Troubleshooting](troubleshooting.md) | `stack doctor`, logs, and the problems seen so far |

## Developer guide

| Page | What it covers |
|---|---|
| [Architecture](architecture.md) | Containers, the `ai-net` network, the router, host profiles, state on disk |
| [The `stack` script](stack-script.md) | The registry, `status --json`, install and doctor, the boot unit |
| [Backends](backends.md) | The KoboldCPP, ComfyUI and Fortuna recipes and their quirks |
| [Decision models](decision-models.md) | An ongoing POC: moving apps' decisions from the LLM to Fortuna, how Whimsy, Cantinero and World Builder changed, and the pattern to follow |
| [Dashboard internals](dashboard-internals.md) | stack-ui: pytincture/wapyt, BFFs, the action allowlist, the systemctl shim, themes |
| [Contributing](contributing.md) | Branches and PRs, tests, the smoke test, rebuilding |
| [Playwright testing](playwright-testing.md) | Browser smoke tests for the pytincture apps: running them, writing one, gotchas |

## Where else to look

- [`README.md`](../../README.md): the repo's front page.
- [`CLAUDE.md`](../../CLAUDE.md): the stack's full working notes. Each component has its own:
  [`router/`](../../router/CLAUDE.md), [`koboldcpp/`](../../koboldcpp/CLAUDE.md),
  [`comfyui/`](../../comfyui/CLAUDE.md), [`fortuna/`](../../fortuna/CLAUDE.md),
  [`stack-ui/`](../../stack-ui/CLAUDE.md).
- Install guides: [`koboldcpp/INSTALL.md`](../../koboldcpp/INSTALL.md),
  [`comfyui/INSTALL.md`](../../comfyui/INSTALL.md).
- Plans: [`HALO_PLAN.md`](../../HALO_PLAN.md) (the AMD Halo box), [`STACK_UI_PLAN.md`](../../STACK_UI_PLAN.md).

When a wiki page and a `CLAUDE.md` disagree, the `CLAUDE.md` is the one kept current with the
code; please fix the wiki page.
