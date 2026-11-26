# Running the stack

[Wiki home](README.md) · User guide

## Two separate settings

Whether the backends **run now** and whether they **start at boot** are independent
(since 2026-11-19).

| You want to… | Command | Dashboard |
|---|---|---|
| start the backends now | `stack on` (same as `stack up`) | **Start backends** |
| stop them now (frontends keep running) | `stack off` (same as `stack down backends`) | **Stop backends** |
| start them at every boot | `stack boot on` | **Start at boot** toggle on |
| keep them off after a reboot | `stack boot off` | **Start at boot** toggle off |
| see the boot setting | `stack boot` | the toggle's label |

`stack on`/`off` never change the boot setting, and `stack boot` never starts or stops anything.
`stack enable`/`disable` are aliases for `stack boot on`/`off`; `stack gaming` is `stack off`.

**To free the GPU for a game:** `stack off`. Add `stack boot off` if it should stay free after a
reboot.

## Everyday commands

```bash
stack                  # interactive: status, offer to start the backends, pick a frontend
stack status           # what is up, and whether the backends start at boot
stack <app>            # start one app: router, koboldcpp, comfyui, fortuna, stackui, or a frontend
stack restart <app>    # restart one app
stack down             # stop everything (also: down backends | down frontends | down <app>)
stack logs <app>       # follow an app's logs
stack frontends        # the installed frontends
stack route [local|halo]   # where text is served (see Text on another machine)
stack gpu-release      # free the text model's GPU memory (art, games); stack gpu-restore loads it again
stack doctor           # check everything, change nothing
```

The backends are the **router** (started first, stopped last), **koboldcpp** (text),
**comfyui** (image and sound) and **fortuna** (decisions). `stack up` exits non-zero if any of
them failed to start, after trying all of them, and names the ones that failed.

`stack down all` leaves the dashboard running, so its own Stop button doesn't kill the page you
pressed it on; stop it with `stack down stackui`.

## Addresses

| Capability | From containers on `ai-net` | From this machine |
|---|---|---|
| Text | `http://koboldcpp:5001` | `http://localhost:5001` |
| Image and sound | `http://comfyui:8188` | `http://localhost:8188` (8189 where `host.env` sets `COMFYUI_PORT`) |
| Decisions | `http://fortuna:8009` | `http://127.0.0.1:8009` (this machine only) |
| Dashboard | — | `http://localhost:7863`, or `http://<host>.local:7863` on the LAN |

All three capabilities go through the router, so these addresses stay the same whichever copy
serves them.

## Freeing VRAM on a 12 GB card

With the stack idle, KoboldCPP (~6 GB for a 9B Q4), Fortuna (~3.3 GB) and ComfyUI fill most of a
12 GB card. To make room for image work:

- **Run Fortuna on the CPU:** `NGL=0` in `fortuna/fortuna.env` (or the dashboard: Language models →
  Fortuna → GPU layers…), then `stack restart fortuna`. Remove the line to put it back on the GPU.
- **Release ComfyUI's memory** after a job: *Release ComfyUI VRAM* on the dashboard.
- **Release the text model** for a heavy session (FLUX, Kontext, a game): `stack gpu-release`
  unloads KoboldCPP's model and frees all of its memory (about 6 GB for a 9B Q4), and
  `stack gpu-restore` loads the same model again in seconds. The dashboard has both: **Release
  VRAM** on the text card, **Restore** once it is released, and a button on the VRAM planner.
  Until it is restored, every app's text requests fail; `stack status` shows the local copy as
  `released`, and `stack up` restores it.

## Timing

A cold start loads the text model, ComfyUI and Fortuna one after another. KoboldCPP alone can
take 20–30 s for a model swap and longer for a large model; the boot unit allows 15 minutes.
