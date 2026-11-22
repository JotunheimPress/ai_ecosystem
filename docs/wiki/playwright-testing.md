# Playwright testing

[Wiki home](README.md) · Developer guide

The dashboard and the pytincture frontends run their UI as **Python inside the browser**
(Pyodide). Most of what breaks there — an import that fails in Pyodide, a widget that never
renders, a BFF call that errors, a theme that doesn't apply — only shows up in a real browser.
Unit tests can't see it. So every pytincture app has a **Playwright smoke test**: a short Python
script that opens the running app in Chromium, walks through every screen, takes a screenshot at
each step, and fails if the browser logged any error.

Unit tests (`pytest`) still cover the server side: BFFs, backends, prompt builders, against fakes.
Playwright covers the browser side. A PR that touches the UI runs both.

## Where the smoke tests are

| App | Script | What it walks |
|---|---|---|
| Dashboard (`stack-ui`) | [`stack-ui/tests/smoke.py`](../../stack-ui/tests/smoke.py) | Every screen, dark and light, every theme, theme survives a reload, Fortuna table, one HuggingFace search |
| Iguana Studio | `iguana_studio/comfyui-auto/tests/smoke.py` | Every mode, the gallery and its menu, the image viewer; `SMOKE_GENERATE=1` also runs one real generation |

pytincture itself has a much larger suite (Chromium, Firefox and WebKit, login, service worker,
streaming BFFs) in its own repo, `docs/e2e-testing.md`. You only need that if you change
pytincture.

## One-time setup

Nothing is installed globally. `uv` pulls Playwright in for each run.

```bash
# Download Playwright's Chromium (lands in ~/.cache/ms-playwright/)
uv run --with playwright python -m playwright install chromium
```

`CHROME` is optional. Leave it unset and Playwright uses the Chromium it just downloaded. Set it
to a particular build if you need one, e.g.
`CHROME=~/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`.

## Running one

Always test a **dev instance on a side port**, not the live container:

```bash
# Dashboard: terminal 1, the dev server (7863 is the real one)
cd stack-ui
STACK_UI_PORT=7873 .venv/bin/python service.py

# terminal 2, unit tests then the smoke test
.venv/bin/python -m pytest -q tests
STACKUI_URL=http://127.0.0.1:7873 uv run --with playwright python tests/smoke.py
```

```bash
# Iguana Studio
IGUANA_URL=http://127.0.0.1:7862 uv run --with playwright python tests/smoke.py
```

The run prints every console error it saw, then `N console error(s); screenshots in tests/shots`.
**Exit 0 means pass.** Open the screenshots in `tests/shots/` (git-ignored) and look at them. The
script can't tell that a layout is ugly; you can.

Useful switches:

| Variable | Effect |
|---|---|
| `STACKUI_URL` / `IGUANA_URL` | Which instance to test |
| `CHROME` | Path to a specific Chromium binary |
| `STACKUI_OFFLINE=1` | Dashboard: skip the HuggingFace search (no network) |
| `SMOKE_GENERATE=1` | Iguana Studio: run one real FLUX job (needs ComfyUI and the model); it deletes the image afterwards |

## How a smoke test is built

Every smoke script follows the same steps. To add one to a new app, copy this skeleton:

```python
"""Smoke test: open every screen, fail on any browser console error, screenshot each.

    APP_URL=http://127.0.0.1:7899 uv run --with playwright python tests/smoke.py
"""
import os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = os.environ.get("APP_URL", "http://127.0.0.1:7899").rstrip("/")
CHROME = os.path.expanduser(os.environ.get("CHROME", "")) or None
SHOTS = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/shots")
SCREENS = ["home", "settings", "about"]

def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        # 1. Collect every browser-side error (JS and Python-in-Pyodide both end up here).
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
        # 2. Wait for Pyodide to boot: wait for something only the app renders. The first
        #    load is slow (Pyodide + first BFF call), hence the long timeout.
        page.goto(f"{URL}/#/{SCREENS[0]}")
        page.wait_for_selector(".my-first-card", timeout=120_000)
        # 3. Walk every screen and take a screenshot of each.
        for screen in SCREENS:
            page.evaluate(f"location.hash = '#/{screen}'")
            page.wait_for_timeout(2000)
            page.screenshot(path=str(SHOTS / f"{screen}.png"))
        # 4. Interactions + asserts on state, e.g.
        #    page.click('[data-theme="dusk"]')
        #    assert page.evaluate("document.documentElement.dataset.theme") == "dusk"
        browser.close()
    real = [e for e in errors if "favicon" not in e]
    for e in real:
        print("ERROR", e)
    print(f"{len(real)} console error(s); screenshots in {SHOTS}")
    return 1 if real else 0  # 5. Non-zero exit = fail

if __name__ == "__main__":
    raise SystemExit(main())
```

The house rules:

1. **Real browser, running app.** No mocking the page. The script points at a server you started.
2. **Any console error fails the run.** Filter only known noise (favicon). Don't widen the filter
   to make a test pass; fix the error.
3. **Read-only by default.** A smoke test never presses start, stop, delete or anything that
   changes the host. Anything that writes or uses the network goes behind an opt-in variable
   (`SMOKE_GENERATE=1`) or an opt-out one (`STACKUI_OFFLINE=1`), and cleans up after itself.
4. **Screenshot every step**, into `tests/shots/` (git-ignored), so a reviewer can see the result.
5. **Navigate by hash route** (`location.hash = '#/screen'`) instead of clicking through menus,
   unless the click is what you're testing.
6. **Wait on selectors, not just time.** Use `wait_for_selector` / `wait_for_function` for
   anything the app renders. Short `wait_for_timeout` pauses are fine between screens so BFF
   data can arrive.
7. **Document how to run it** in the script's docstring and in the app's `CLAUDE.md`.

## Gotchas

- **Native browser dialogs can't be driven.** Playwright can't click Chromium's file pickers.
  Stub them before the app loads (`page.add_init_script(...)`), e.g. delete
  `window.showOpenFilePicker` so the app falls back to an `<input type=file>` that Playwright
  *can* fill with `set_input_files`, or replace `showSaveFilePicker` with an in-memory handle
  that returns a real `WritableStream`. Removing the pickers entirely also tests the code path
  Firefox takes, since Firefox doesn't have them.
- **First load is slow.** Pyodide plus package downloads can take tens of seconds on a cold
  cache. Keep the 120 s timeout on the first selector.
- **Async is fine too.** Iguana Studio's smoke uses `playwright.async_api`. Both styles are fine;
  stay consistent within an app.
- **Dark and light.** If the app has themes, run at least the first screen with
  `new_page(color_scheme="light")` as well as dark.
- **Leftover state.** Things like theme choice live in `localStorage`. If a test changes them,
  reset them (`localStorage.removeItem(...)` + reload) before carrying on.

## In a PR

There's no CI. Run the unit tests and the smoke test yourself, look at the screenshots, and say
in the PR what you ran (and anything you skipped, like `SMOKE_GENERATE`). See
[Contributing](contributing.md).
