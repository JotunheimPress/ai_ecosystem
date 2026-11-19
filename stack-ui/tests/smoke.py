"""Smoke test: open every screen, fail on any browser console error, screenshot each.

    STACKUI_URL=http://127.0.0.1:7873 \
    CHROME=~/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome \
      uv run --with playwright python tests/smoke.py [shots_dir]

Read-only: it never presses a button that starts, stops or deletes anything. It does run
one HuggingFace search (network) unless STACKUI_OFFLINE=1.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = os.environ.get("STACKUI_URL", "http://127.0.0.1:7863").rstrip("/")
CHROME = os.path.expanduser(os.environ.get("CHROME", ""))
SHOTS = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/shots")
THEMES = ["bone", "ember", "moss", "dusk", "harbor", "daylight"]
SCREENS = ["infrastructure", "frontends", "llms", "images", "vram", "logs", "nodes", "doctor"]


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME or None)
        for scheme in ("dark", "light"):
            page = browser.new_page(viewport={"width": 1440, "height": 900},
                                    color_scheme=scheme)
            page.on("console", lambda m: errors.append(f"[{scheme}] {m.text}")
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(f"[{scheme}] pageerror {e}"))
            page.goto(f"{URL}/stackui/#/infrastructure")
            # Pyodide boot + first BFF round trip.
            page.wait_for_selector(".su-card-title", timeout=120_000)
            page.wait_for_timeout(1500)
            page.screenshot(path=str(SHOTS / f"{scheme}-infrastructure.png"))
            if scheme == "light":
                break
            for screen in SCREENS[1:]:
                page.evaluate(f"location.hash = '#/{screen}'")
                page.wait_for_timeout(2500)
                page.screenshot(path=str(SHOTS / f"{scheme}-{screen}.png"))
            # Every theme, on the overview: click its swatch under the sidebar.
            page.evaluate("location.hash = '#/infrastructure'")
            page.wait_for_timeout(1200)
            for theme in THEMES:
                page.click(f'.su-swatch[data-theme="{theme}"]')
                page.wait_for_timeout(500)
                assert page.evaluate("document.documentElement.dataset.sutheme") == theme, theme
                page.screenshot(path=str(SHOTS / f"theme-{theme}.png"))
            # The choice survives a reload (localStorage, per browser).
            page.click('.su-swatch[data-theme="ember"]')
            page.reload()
            page.wait_for_selector(".su-card-title", timeout=120_000)
            assert page.evaluate("document.documentElement.dataset.sutheme") == "ember"
            # Back to the OS default for the rest of the run.
            page.evaluate("localStorage.removeItem('stackui.theme')")
            page.reload()
            page.wait_for_selector(".su-card-title", timeout=120_000)
            # Language models -> Fortuna: the pinned table with each file's integrity.
            page.evaluate("location.hash = '#/llms'")
            page.wait_for_timeout(1500)
            page.get_by_text("Fortuna", exact=True).first.click()
            page.wait_for_selector("#ft-status b", timeout=15_000)
            page.screenshot(path=str(SHOTS / f"{scheme}-llms-fortuna.png"))
            if not os.environ.get("STACKUI_OFFLINE"):
                page.evaluate("location.hash = '#/llms'")
                page.wait_for_timeout(1500)
                page.get_by_text("Find on HuggingFace").click()
                page.fill("#hf-q", "qwen3 8b")
                page.click("#hf-go")
                page.wait_for_timeout(5000)
                first = page.locator(".su-split-left tbody tr").first
                first.click()
                page.wait_for_timeout(5000)
                page.screenshot(path=str(SHOTS / f"{scheme}-llms-find.png"))
            page.close()
        browser.close()
    real = [e for e in errors if "favicon" not in e]
    for e in real:
        print("ERROR", e)
    print(f"{len(real)} console error(s); screenshots in {SHOTS}")
    return 1 if real else 0


if __name__ == "__main__":
    raise SystemExit(main())
