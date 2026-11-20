# Frontends

[Wiki home](README.md) · User guide

A frontend is an app that uses the backends. Each is its own repo, cloned **next to** ai-stack,
with a `frontend.json` at its root. The stack lists exactly what is installed:

```bash
stack frontends          # repo name, brand, port, directory
stack <repo>             # start one by its repo name
stack down <repo>        # stop it
stack logs <repo>
```

Frontends run side by side on their own ports, so several can be up at once. The backends'
ports (5001, 8188, 8009) and the dashboard's (7863) are taken; pick something else for yours.

## Installing and removing

- **Install:** clone the repo into the ecosystem root, next to `ai-stack/`. It builds its own
  image on its first `stack <repo>`.
- **Remove:** stop it and delete the clone.

## `frontend.json`

```json
{
  "repo": "my-app",
  "brand": "My App",
  "description": "What it does, in one line",
  "port": 7870,
  "health": "http://127.0.0.1:7870/api/health",
  "container": "my-app",
  "start": "run-podman.sh",
  "icon": "mdi-robot-outline",
  "https_port": 7871,
  "https_setup": "/ca"
}
```

- `repo` is the identity: what you type after `stack`. `brand` is only what screens show.
- A fork must change `repo`, `port` and `container`.
- `https_port` / `https_setup` (optional): the same app over HTTPS, so a phone's browser allows the
  microphone. `https_setup` is a page on the HTTP port that explains trusting its certificate.
- A broken manifest is skipped and reported by `stack doctor`, `stack frontends` and the
  dashboard's Frontends workspace, never fatal. A `repo` that clashes with a stack command or a
  built-in app is refused.

## Reaching the backends

From inside a frontend's container (on `ai-net`): `http://koboldcpp:5001`,
`http://comfyui:8188`, `http://fortuna:8009`. These names belong to the router, so they keep
working when text moves to another machine.
