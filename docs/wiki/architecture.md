# Architecture

[Wiki home](README.md) · Developer guide

## The pieces

```
                  frontends (own repos, own ports)          AI Ecosystem dashboard (:7863)
                          │  http://koboldcpp:5001                │ drives `stack`
                          │  http://comfyui:8188                  │ (podman socket, session bus)
                          │  http://fortuna:8009                  ▼
                          ▼                                     stack
   ai-net ──────── backend-router (nginx) ───────────────────────────────────────────
                    │            │             │
           koboldcpp-local   comfyui-local   fortuna-local        (no host ports)
           or the Halo box
```

- **Everything is a Podman container** on one network, **`ai-net`**: the router, the three
  backends, every frontend and the dashboard.
- **The router** (`backend-router`, stock `nginx:1.28-alpine`) owns the names `koboldcpp`,
  `comfyui` and `fortuna` on `ai-net` and their host ports (5001, the ComfyUI port, and
  `127.0.0.1:8009`). It forwards to the `*-local` containers, which publish nothing; text can go to
  another machine instead. Details: [`router/CLAUDE.md`](../../router/CLAUDE.md).
- **Anything about "the copy on this GPU" bypasses the router** with `podman exec` into the
  `*-local` container, because through the router the request could reach another machine:
  `stack status`'s local probes, Fortuna's start wait, KoboldCPP asking ComfyUI to free memory.
- **The dashboard** is a container too, but it controls the host: it mounts the podman socket
  and the user's session bus, and runs with `--network=host`. See
  [Dashboard internals](dashboard-internals.md).

## Host profiles

`lib/host.sh` (sourced by `stack` and every start script) resolves the **host profile** from
the environment, an optional `host.env`, or detection:

| Setting | Values | Effect |
|---|---|---|
| `GPU` | `nvidia`, `amd`, `none` | GPU flags for every container, image tags. Detection tries NVIDIA CDI first (a workstation with an AMD CPU also has `/dev/kfd`) |
| `LLM_BACKEND` | `cuda`, `vulkan`, `rocm` | KoboldCPP and Fortuna images |
| `BIND` | an IPv4 address | which address published ports bind to |
| `COMFYUI_PORT` | a port | ComfyUI's host port (8189 on AMD Rex, whose OS owns 8188) |

Both profiles stay supported: the 12 GB RTX 4070 workstation (NVIDIA) and the Strix Halo box
(AMD, 128 GB unified memory). See [`HALO_PLAN.md`](../../HALO_PLAN.md).

## Code versus state

The **recipes** (Containerfiles, start scripts, configs) live in this repo. **State** lives in
sibling directories under the ecosystem root and is never committed:

| Directory | Holds |
|---|---|
| `koboldcpp/` | links back into `ai-stack/koboldcpp/`, plus `models/`, `model.env`, `model-presets.json` |
| `ComfyUI/` | `models/`, `input/`, `output/`, `user/` (incl. `comfyui.db`), `custom_nodes/` (the code is in the image) |
| `fortuna/` | `models/` (with `.verified`/`.mismatch` stamps), `fortuna.env` |
| `router/` | `route.conf`, written only by `stack route` |
| `model-sources.json` | where each file the dashboard downloaded came from |

The root is `$AI_ECOSYSTEM` if set, else the checkout's parent. Never hard-code a path.

## Start at boot

A **systemd user** unit, `ai-stack-backends.service` (oneshot, `ExecStart=stack up`,
`ExecStop=stack down backends`), rendered from a template for this checkout by
`stack install service`. Its enablement is the start-at-boot setting (`stack boot on|off`); it is
never used to start or stop the backends now. See [The `stack` script](stack-script.md).
