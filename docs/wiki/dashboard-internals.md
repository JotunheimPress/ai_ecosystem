# Dashboard internals (stack-ui)

[Wiki home](README.md) · Developer guide

`stack-ui/` is **AI Ecosystem**. The page is Python running in the browser (Pyodide), built from
**wapyt** widgets and served by **pytincture**. Full notes:
[`stack-ui/CLAUDE.md`](../../stack-ui/CLAUDE.md); read them before changing it.

## How the page talks to the server

- **BFF calls** (`await StackApi().action_async(...)`, `ModelsApi`) for request/response. Every
  result is `{"ok": ...}`; expected failures never raise.
- **GET event streams** in `service.py` for what outlives a request: action output, log tails,
  download progress.
- BFF modules are re-executed on every call, so all state lives in the plain `services/*` modules.

## The security boundary

The dashboard has no login and answers on the LAN, and it can drive the host. So:

- **`services/stackctl.py` `VERBS` is the allowlist.** A verb key maps to a fixed `stack` argv;
  a verb not in the table cannot run. A new entry is the change that deserves review.
- **Targets come from live state, never the request:** app names from `stack status`, Fortuna
  model names from `models.table`. Routing and boot are fixed verbs with no target.
- No shell: every call is a list argv. Subprocesses get a minimal environment.
- Files are named by collection key + basename through `inventory._safe_target()`; a request never
  carries a path. Downloads take a repo/path or a Civitai version id; the URL is looked up server
  side, and only huggingface.co and civitai.com are allowed.
- pytincture's origin guards refuse cross-site writes.
- One action at a time: a second is refused (409), not queued.

## Running inside a container that controls the host

| Mount / flag | Why |
|---|---|
| podman socket's directory | `stack` drives the host engine (the image has the podman *client* only) |
| session bus | `stack boot` is `systemctl --user` |
| `--network=host` | health probes on `localhost` |
| `--userns=keep-id` | the session bus checks the host-visible uid |
| the ecosystem at its real path | podman resolves `-v` paths on the host |

`systemctl --user` can't reach the host's user manager from a container, so `shim/systemctl`
implements the few calls `stack` makes as D-Bus calls on the session bus.

## Themes

`ui/themes.py` sets `<html data-sutheme>` and wapyt's dark/light mode; the choice is kept in
localStorage. `static/stackui.css` defines each palette as variables, maps wapyt's tokens onto
them, and overrides the accents wapyt hard-codes in blue.

## Developing

See [Contributing](contributing.md#the-dashboard) for the dev server, tests and the smoke test.
