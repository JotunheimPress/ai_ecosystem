# Frontends

[Wiki home](README.md) · User guide

A frontend is an app that uses the backends. Each is its own repo, cloned **next to** ai-stack,
with a `frontend.json` at its root. The stack lists exactly what is installed:

```bash
stack frontends          # repo name, brand, port, directory
stack <repo>             # start one, e.g. stack companion
stack down <repo>        # stop it
stack logs <repo>
```

Frontends run side by side on their own ports. Installed on the Halo box today, for example:
`companion` (Whimsy, 7861), `ai-story-maker` (7860), `iguana_studio` (7862), `nocturne` (7864),
`npc_maker` (7865), `narrative_engine` (7866), `companion-assistant` (Cantinero, 7867).

## Installing and removing

- **Install:** clone the repo into the ecosystem root, next to `ai-stack/`. It builds its own
  image on its first `stack <repo>`. These are private repos; ask for access.
- **Remove:** stop it and delete the clone.

## `frontend.json`

```json
{
  "repo": "companion",
  "brand": "Whimsy",
  "description": "Character-card AI companion",
  "port": 7861,
  "health": "http://127.0.0.1:7861/api/health",
  "container": "companion",
  "start": "run-podman.sh",
  "icon": "mdi-account-heart-outline",
  "https_port": 7868,
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
