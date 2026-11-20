# Contributing

[Wiki home](README.md) · Developer guide

## Workflow

- `master` is the default branch. Work on a branch named `feature/…`, `fix/…`, `docs/…` or
  `chore/…`, open a PR to `master`, and squash-merge it.
- Commit and PR titles are `prefix: plain sentence` saying what is different for someone using
  it, e.g. `fix: ComfyUI keeps its database with the rest of its state`.
- The `CLAUDE.md` next to the code you change is part of the change: keep it current. This wiki
  summarises; update it when behaviour a user sees changes.
- This wiki's source is `docs/wiki/` in the repo; the GitHub wiki is built from it. Edit the
  Markdown there, in a PR, and after it merges run `docs/publish-wiki.sh` (`--dry-run` first to
  see what changes). Edits made directly on GitHub are overwritten by the next publish.
- No CI: run the checks below before opening a PR, and say in the PR what you ran and what you
  didn't.

## Releases

AI Ecosystem uses SemVer in [`VERSION`](../../VERSION). Add a line to *Unreleased* in
[`CHANGELOG.md`](../../CHANGELOG.md) for anything a user would notice. To release: a PR that bumps
`VERSION` and moves *Unreleased* under the new version's heading; after it merges, tag
`vX.Y.Z` on `master` and create a GitHub release with that section. When to bump which number
is in `CLAUDE.md` → *Versioning and releases*.

## The `stack` script and start scripts

```bash
bash -n stack fortuna/start.sh        # syntax
./stack doctor                        # read-only, safe on a live host
./stack status --json | python3 -m json.tool
```

## The dashboard

```bash
cd stack-ui
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python "pytincture==1.0.0" "uvicorn[standard]" jeepney pytest pip \
    <path to wa_pytincture_widgetset>
PYTHON=$PWD/.venv/bin/python <path to wa_pytincture_widgetset>/scripts/dev_wheel.sh appcode

STACK_UI_PORT=7873 .venv/bin/python service.py     # dev server; 7863 is the real one
.venv/bin/python -m pytest -q tests                 # unit tests, against fakes
STACKUI_URL=http://127.0.0.1:7873 CHROME=<chromium> \
  uv run --with playwright python tests/smoke.py    # every screen and theme; fails on any console error
```

- The dev server drives the real host (podman, systemd), so it is a real control plane, not a
  mock. The smoke test only reads, plus one HuggingFace search (`STACKUI_OFFLINE=1` skips it)
  and theme clicks, which only touch the browser's storage.
- After merging (or pulling), `stack install stackui` rebuilds the dashboard if `stack-ui/` has
  changed and restarts it on the new image. `stack-ui/run-podman.sh --rebuild` forces a build.

## Backends

Changing a recipe usually means rebuilding its image and restarting it:
`podman rmi <image>` (or bump the pinned version) and `stack restart <backend>`. A restart
interrupts whatever the frontends are doing with that backend, so check first (ComfyUI's queue:
`curl localhost:8189/queue`).
