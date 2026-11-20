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

**Official frontends** are listed in the stack itself (`frontends.json`, updated with each
release). The dashboard's Frontends screen installs, updates and removes them with a button; from
a terminal:

```bash
stack frontend available          # what's listed, and whether each is installed
stack frontend add <name>         # clone its latest release next to ai-stack, check it, build it
stack frontend update <name>      # its newest release (or git pull on a branch), rebuilt
stack frontend remove <name>      # stop it and delete its image and folder
stack frontend check              # is a newer release out for any installed frontend?
stack frontend add <git URL>      # any other frontend: see "Any other frontend" below
```

- `add <name>` takes names from the list. It refuses before cloning if the frontend's port or
  container name is already taken, or if it needs a newer AI Ecosystem, and refuses after
  cloning if the frontend's own `frontend.json` doesn't match the list.
- `check` asks each installed frontend's origin for its release tags (`vX.Y.Z`) and changes
  nothing. A frontend counts as behind when the newest tag isn't in its history yet, so a clone
  you work in on a branch past that tag is up to date. The dashboard runs the same check
  (cached for 6 hours, again after an install, update or remove) and shows "vX.Y.Z available"
  on the frontend's card and a count beside *Installed apps*. `STACK_UPDATE_CHECK=off` turns it
  off. A private GitHub repo is checked with `GITHUB_TOKEN` (below); without one the dashboard
  can't check it, since it has no git login of its own.
- `remove` won't delete a folder holding anything git doesn't: saved data, local changes or
  unpushed commits. It lists them; move what you want to keep and delete the folder yourself.

**Any other frontend** installs from its git URL, the same way as an official one: newest
release, checked, built. Nobody reviewed it, so `add` shows what it is and what it will run, and
asks first (`--yes` skips the question, and is needed without a terminal):

```bash
stack frontend add https://github.com/someone/my-app.git
stack frontend add git@github.com:someone/my-app.git
```

- The repo's name is the frontend's name, and its `frontend.json` must say the same `repo`.
- It refuses a port or container name that's already taken, and an official frontend's name
  under another URL: a fork needs its own name. Taking a port the official list reserves is
  allowed, with a warning: you won't be able to install both.
- The dashboard marks it *Custom*. `stack frontend update`, `check` and `remove` work on it.
- Cloning it into the ecosystem root yourself, next to `ai-stack/`, still works too; it builds
  its image on its first `stack <repo>`.

**Private GitHub repos:** your own git login (gh, a credential manager, ssh) works from a
terminal. Otherwise, and always in the dashboard, put a `GITHUB_TOKEN=…` line with read access
to the repo in `stack-ui/tokens.env` (keep it `chmod 600`; rebuild the dashboard for it to see
it). git is offered it for github.com only, after your own login, and it is never written into
the clone.

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
- `build` (optional, not in the example): a script in the repo plus plain arguments, e.g.
  `"run-podman.sh --build-only"`, that builds the image without starting it. `stack frontend add`
  and `update` run it, so the first start is quick.
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
