"""
Is a newer release out for any installed frontend? **Server side**, like ``release_check``:
a plain module (not a BFF) so the cache survives pytincture re-executing BFF modules on every
call; ``StackApi.frontend_updates`` asks it.

The check itself is ``stack frontend check --json`` (``stackctl.VERBS["frontend_check"]``):
``git ls-remote`` on each installed frontend's origin, in parallel, fetching nothing. It runs
in this container, where git has no login, so a private frontend repo reads as
``reason: "unreachable"``; a public one needs nothing.

Remembered for ``CHECK_TTL`` (a failed run for ``FAILURE_TTL``), shared by every tab. After an
install, update or remove the dashboard asks with ``force`` so the badge follows at once.
``STACK_UPDATE_CHECK=off`` turns this off along with the AI Ecosystem release check.
"""
from __future__ import annotations

import json
import threading
import time

from services.release_check import checks_enabled

CHECK_TTL = 6 * 3600
FAILURE_TTL = 30 * 60

_cache: dict = {}
_lock = threading.Lock()


def frontend_updates(now: float | None = None, force: bool = False) -> dict:
    """{checked: bool, frontends: {repo: {current, latest, newer, checked, reason, origin}},
    reason?: off|unreachable, error?}. Never raises: being offline is a normal state."""
    from services import stackctl

    if not checks_enabled():
        return {"checked": False, "reason": "off", "frontends": {}}
    now = time.time() if now is None else now
    with _lock:
        if not force and _cache and now < _cache["until"]:
            return json.loads(json.dumps(_cache["result"]))
    try:
        rows = json.loads(stackctl.run("frontend_check")["stdout"]).get("frontends")
        if not isinstance(rows, list):
            raise ValueError("no frontends list")
        result = {"checked": True, "frontends": {
            str(r["repo"]): {k: r.get(k) for k in ("current", "latest", "newer", "checked", "reason", "origin")}
            for r in rows if isinstance(r, dict) and r.get("repo")}}
        ttl = CHECK_TTL
    except Exception as exc:  # noqa: BLE001 - a failed check is shown as "not checked"
        result = {"checked": False, "reason": "unreachable", "frontends": {},
                  "error": f"{type(exc).__name__}: {exc}"[:200]}
        ttl = FAILURE_TTL
    with _lock:
        _cache.update(until=now + ttl, result=result)
    return json.loads(json.dumps(result))
