"""Colour themes, picked from swatches at the foot of the sidebar — the way Whimsy does it.

Five dark colourways share Whimsy's names and palettes (Bone, Ember, Moss, Dusk, Harbor), so the
dashboard and the companion read as one family; Daylight is the light one. Every colour is a CSS
variable set by ``<html data-sutheme="…">`` (static/stackui.css), and wapyt's own widgets are
pointed at the same variables, so a theme change restyles everything at once.

The choice is a per-browser convenience kept in localStorage — there are no accounts here. With
nothing stored, the page follows the OS: Harbor when it prefers dark, Daylight when light.
Storage can be missing or throw (private windows, blocked site data), so every access is guarded
and the page still renders with the default.
"""
from __future__ import annotations

import js

# (id, label, swatch colour, dark?)
THEMES = [
    ("bone", "Bone", "#f5f1e8", True),
    ("ember", "Ember", "#e8a33d", True),
    ("moss", "Moss", "#9ec978", True),
    ("dusk", "Dusk", "#b79df0", True),
    ("harbor", "Harbor", "#7fb4d8", True),
    ("daylight", "Daylight", "#2563eb", False),
]
KEY = "stackui.theme"
_BY_ID = {t[0]: t for t in THEMES}


def _stored() -> str:
    try:
        v = js.window.localStorage.getItem(KEY)
        return str(v) if v else ""
    except Exception:  # noqa: BLE001 - storage blocked or unavailable
        return ""


def _store(theme: str) -> None:
    try:
        js.window.localStorage.setItem(KEY, theme)
    except Exception:  # noqa: BLE001
        pass


def default() -> str:
    try:
        dark = bool(js.window.matchMedia("(prefers-color-scheme: dark)").matches)
    except Exception:  # noqa: BLE001
        dark = True
    return "harbor" if dark else "daylight"


def current() -> str:
    t = _stored()
    return t if t in _BY_ID else default()


def apply(app, theme: str, remember: bool = False) -> str:
    """Paint ``theme`` (falls back to the default) and tell wapyt whether it is dark."""
    theme = theme if theme in _BY_ID else default()
    js.document.documentElement.setAttribute("data-sutheme", theme)
    app.set_theme("dark" if _BY_ID[theme][3] else "light")
    if remember:
        _store(theme)
    label = js.document.getElementById("su-theme-name")
    if label:
        label.textContent = _BY_ID[theme][1]
    for el in js.document.querySelectorAll(".su-swatch"):
        el.classList.toggle("on", str(el.getAttribute("data-theme")) == theme)
    return theme


def picker_html() -> str:
    swatches = "".join(
        f'<button class="su-swatch" data-theme="{t}" title="{label}" aria-label="{label} theme">'
        f'<i style="background:{dot}"></i></button>'
        for t, label, dot, _ in THEMES)
    return (f'<div class="su-themes"><div class="su-themes-head"><span>Theme</span>'
            f'<b id="su-theme-name"></b></div><div class="su-swatches">{swatches}</div></div>')
