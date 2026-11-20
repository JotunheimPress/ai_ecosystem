"""
Is there a newer AI Ecosystem release? **Server side**: the browser cannot reach GitHub
(pytincture's CSP allows ``connect-src 'self'`` only). A plain module, not a BFF, so the
cache survives pytincture re-executing BFF modules on every call; ``StackApi.latest_release``
asks it. Modelled on IguanaXterm's ``release_check.py``.

One request to GitHub's "latest release" API, shared by everyone using this dashboard and
remembered for ``CHECK_TTL`` (a failure for ``FAILURE_TTL``), so the 60-an-hour anonymous
limit is never near. Nothing is sent but the request, an ``AI-Ecosystem/<version>``
User-Agent and, when set, the token.

**The repo is private**, and GitHub answers 404 for a private repo's releases without a
token. So ``GITHUB_TOKEN`` (``stack-ui/tokens.env``, like ``HF_TOKEN``; a fine-grained token
with read-only *Contents* on ``JotunheimPress/ai-stack`` is enough) is sent as a bearer
header when set, and only to api.github.com. Without one, a 404 is reported as
``reason: "private"`` so the About screen can say what to do, rather than "unreachable".
``stack version --check`` needs no token: it asks through git, with your own login.

``STACK_UPDATE_CHECK=off`` (or ``0``/``false``/``no``) turns the check off.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

from services.about import RELEASES_URL, REPO, is_newer, parse_version

LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_TTL = 6 * 3600
FAILURE_TTL = 30 * 60
TIMEOUT = 5

_cache: dict = {}
_lock = threading.Lock()


def checks_enabled() -> bool:
    return os.environ.get("STACK_UPDATE_CHECK", "on").strip().lower() not in (
        "off", "0", "false", "no")


def current_version() -> str:
    """This checkout's release, from ai-stack/VERSION (what `stack version` reads)."""
    from services.stackctl import STACK

    try:
        return (STACK.parent / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def _token() -> str:
    return os.environ.get("GITHUB_TOKEN", "").strip()


def _fetch(version: str) -> dict:
    """GitHub's latest release, as JSON. Raises on any failure."""
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": f"AI-Ecosystem/{version}"}
    if _token():
        headers["Authorization"] = f"Bearer {_token()}"
    request = urllib.request.Request(LATEST_URL, headers=headers)
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed https URL
        return json.loads(response.read(256 * 1024).decode("utf-8"))


def _reason(exc: Exception) -> str:
    """Why a check failed, in words the About screen can act on."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404 and not _token():
            return "private"            # what GitHub says about a private repo, anonymously
        if exc.code in (401, 403, 404) and _token():
            return "token"              # sent one, and it can't read this repo
    return "unreachable"


def latest_release(now: float | None = None, force: bool = False) -> dict:
    """The check's result, from the cache while it is fresh (``force``: ask GitHub anyway,
    for About's "Check again").

    {current, checked: bool, latest?, newer?, url?, published?, reason?: off|private|token|
    unreachable, error?}. Never raises: being offline is a normal state.
    """
    version = current_version()
    if not checks_enabled():
        return {"current": version, "checked": False, "reason": "off"}
    now = time.time() if now is None else now
    with _lock:
        if not force and _cache and now < _cache["until"] and _cache["result"].get("current") == version:
            return dict(_cache["result"])
    try:
        body = _fetch(version)
        tag = str(body.get("tag_name") or "")
        if parse_version(tag) is None:
            raise ValueError(f"unexpected release tag {tag!r}")
        latest = tag.lstrip("v")
        result = {
            "current": version, "checked": True, "latest": latest,
            "newer": is_newer(latest, version),
            "url": str(body.get("html_url") or RELEASES_URL),
            "published": str(body.get("published_at") or "")[:10],
        }
        ttl = CHECK_TTL
    except Exception as exc:  # noqa: BLE001 - offline is normal; say so, don't fail
        result = {"current": version, "checked": False, "reason": _reason(exc),
                  "error": f"{type(exc).__name__}: {exc}"[:200]}
        ttl = FAILURE_TTL
    with _lock:
        _cache.update(until=now + ttl, result=result)
    return dict(result)

