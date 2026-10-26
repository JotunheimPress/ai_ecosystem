"""Browser-side helpers shared by every screen. Runs in Pyodide only.

pytincture ships every local module the page imports, so screens live in ``ui/`` and
import *names* from here (``from ui.common import spawn``), never the module itself: the
browser packager follows only the first form.
"""
from __future__ import annotations

import asyncio
import html
import json
import time
import traceback

import js
from pyodide.ffi import create_proxy

from wapyt import message


def spawn(coro, label: str):
    """Run a coroutine detached, but never silently — ensure_future swallows errors."""

    async def _guarded():
        try:
            await coro
        except Exception:  # noqa: BLE001 - last line of defence
            js.console.error(f"[stackui] {label} failed:\n{traceback.format_exc()}")

    return asyncio.ensure_future(_guarded())


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def toast(text: str, kind: str = "info", timeout_ms: int = 4000) -> None:
    message.toast(text, kind=kind, timeout_ms=timeout_ms)


def failed(result, what: str) -> bool:
    """True (and a toast) when a BFF result is an error: ``if failed(r, "x"): return``."""
    if isinstance(result, dict) and result.get("ok"):
        return False
    error = result.get("error") if isinstance(result, dict) else None
    toast(f"Could not {what}: {error}" if error else f"Could not {what}.", kind="error",
          timeout_ms=7000)
    return True


def by_id(element_id: str):
    """``getElementById``; a miss is ``JsNull`` (falsy, but not ``None``)."""
    return js.document.getElementById(element_id)


def attr(element, name: str) -> str:
    """``getAttribute`` as a str ('' when missing). ``dataset`` raises in Pyodide."""
    if not element:
        return ""
    value = element.getAttribute(name)
    return str(value) if value else ""


def closest(event, selector: str):
    target = event.target
    return target.closest(selector) if target and hasattr(target, "closest") else None


def fmt_bytes(n) -> str:
    if n is None:
        return "—"
    n = float(n)
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.1f} {units[i]}" if n < 10 and i > 0 else f"{round(n)} {units[i]}"


def ago(epoch) -> str:
    if not epoch:
        return ""
    s = max(0, int(time.time() - float(epoch)))
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


def link(href: str, label: str, title: str = "") -> str:
    """An outbound link, escaped. Off-site pages open in a new tab with no referrer."""
    return (f'<a class="su-link" href="{esc(href)}" target="_blank" '
            f'rel="noopener noreferrer" title="{esc(title or href)}">{esc(label)}'
            f'<i class="mdi mdi-open-in-new"></i></a>')


def search_url(kind: str, name: str) -> str:
    """Where to look for a file nothing recorded the origin of. Labelled *search*."""
    from urllib.parse import quote

    stem = name
    for ext in (".gguf", ".safetensors", ".ckpt", ".pt"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
    q = quote(stem)
    return (f"https://huggingface.co/models?search={q}" if kind == "kobold"
            else f"https://civitai.com/search/models?query={q}")


class Listeners:
    """Owns DOM listeners and their proxies, so a screen can drop them all at once."""

    def __init__(self) -> None:
        self._items: list = []

    def on(self, target, name: str, handler) -> None:
        if not target:
            return
        proxy = create_proxy(handler)
        target.addEventListener(name, proxy)
        self._items.append((target, name, proxy))

    def clear(self) -> None:
        for target, name, proxy in self._items:
            try:
                target.removeEventListener(name, proxy)
                proxy.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._items = []


class Stream:
    """A server-sent event stream decoded to dicts.

    ``on_event(dict)`` per message; ``on_end()`` once, when the stream errors or is closed
    by the server. Closing it from here is what kills a followed ``podman logs -f``.
    """

    def __init__(self, url: str, on_event, on_end=None) -> None:
        self._on_end = on_end
        self._es = js.EventSource.new(url)
        self._msg = create_proxy(self._message)
        self._err = create_proxy(self._error)
        self._es.addEventListener("message", self._msg)
        self._es.addEventListener("error", self._err)
        self._on_event = on_event
        self.open = True

    def _message(self, ev) -> None:
        try:
            self._on_event(json.loads(ev.data))
        except Exception:  # noqa: BLE001
            js.console.error(f"[stackui] stream handler failed:\n{traceback.format_exc()}")

    def _error(self, _ev) -> None:
        # EventSource reconnects on its own after an error; these streams are finite
        # (a job ends, a download ends), so a reconnect would only replay. Stop instead.
        self.close(notify=True)

    def close(self, notify: bool = False) -> None:
        if not self.open:
            return
        self.open = False
        try:
            self._es.close()
            self._es.removeEventListener("message", self._msg)
            self._es.removeEventListener("error", self._err)
        except Exception:  # noqa: BLE001
            pass

        # close() is often called from inside _message (a "done" event); destroying the
        # proxy that is executing right now would fail, so release them on the next tick.
        def _release():
            for proxy in (self._msg, self._err):
                try:
                    proxy.destroy()
                except Exception:  # noqa: BLE001
                    pass
        asyncio.get_event_loop().call_soon(_release)
        if notify and self._on_end:
            self._on_end()


def container_of(cell):
    """The DOM element of a wapyt layout or tab cell (``get_cell`` returns the cell object)."""
    return cell.getContainer() if hasattr(cell, "getContainer") else cell
