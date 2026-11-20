"""BFF: the stack itself — status, the backends' loaded models, actions, nodes, doctor.

pytincture re-executes this module for every call, so it holds nothing: the job slot
lives in `services.jobs` and the registry cache in `services.stackctl`, both plain modules
imported inside the methods (cached in sys.modules as usual).

**Every mutation still goes through `stackctl.VERBS`.** `action()` hands a verb key and a
target to `jobs.start()`, which resolves them against the allowlist and the live registry
exactly as the old `POST /api/action` did. Nothing here builds a command line.

Output of a started action is followed over `GET /api/action/<id>/events` (service.py):
`stack up` can take minutes, far past what a BFF call may hold open.
"""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend


def _err(exc: Exception) -> dict:
    return {"ok": False, "error": str(exc) or exc.__class__.__name__}


@backend_for_frontend
class StackApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    def overview(self) -> dict:
        """Everything the dashboard shows, in one round trip."""
        from services import backends, gpu, jobs, stackctl

        try:
            st = stackctl.status()
        except stackctl.StackError as exc:
            return _err(exc)
        job = jobs.current()
        return {
            "ok": True,
            **st,
            "busy": bool(job and not job.done),
            "job": job.public() if job and not job.done else None,
            "backends": backends.snapshot(),
            "disk": stackctl.disk(),
            "gpu": gpu.read(),
        }

    def action(self, verb: str, target: str = "") -> dict:
        """Start one stack action. Returns at once; follow it over the events route."""
        from services import jobs, stackctl

        v = stackctl.VERBS.get(verb or "")
        if v is None or not v.mutating:
            # Read-only verbs have their own calls; refusing them here keeps this the one
            # unambiguous "changes state" entry point.
            return {"ok": False, "error": f"not an action verb: {verb!r}"}
        try:
            job = jobs.start(verb, target or None)
        except jobs.Busy as exc:
            return {**_err(exc), "busy": True}
        except stackctl.StackError as exc:
            return _err(exc)
        return {"ok": True, "job": job.public()}

    def current_job(self) -> dict:
        from services import jobs

        job = jobs.current()
        return {"ok": True, "job": job.public() if job else None}

    def latest_release(self, force: bool = False) -> dict:
        """Whether a newer AI Ecosystem release is out, for About and the toolbar badge.
        The check and its cache live in `services.release_check` (this module is re-executed
        on every call, so it can't keep them)."""
        from services.release_check import latest_release

        return {"ok": True, **latest_release(force=bool(force))}

    def frontend_catalog(self) -> dict:
        """The official frontends and what is installed (`stack frontend available --json`).
        Installing, updating and removing go through ``action`` like every other change.
        ``url_install``: this browser is on the local network, so it may install a
        frontend from a git URL (``frontend_add_url``, which checks again)."""
        from services import stackctl

        return {"ok": True, **stackctl.catalog(), "url_install": stackctl.REQUEST_LAN.get()}

    def frontend_updates(self, force: bool = False) -> dict:
        """Which installed frontends have a newer release on their origin, for the Frontends
        cards and the sidebar badge. Checked and cached in `services.frontend_updates`."""
        from services.frontend_updates import frontend_updates

        return {"ok": True, **frontend_updates(force=bool(force))}

    def doctor(self) -> dict:
        """Not polled — it shells out to podman several times. On demand only."""
        from services import stackctl

        try:
            res = stackctl.run("doctor")
        except stackctl.StackError as exc:
            return _err(exc)
        return {"ok": True, **stackctl.parse_doctor(res["output"]), "raw": res["output"],
                "duration_ms": res["duration_ms"]}

    def nodes(self) -> dict:
        """Pinned commits vs what is checked out, read from `stack`'s own registry."""
        from services import nodes

        try:
            return {"ok": True, **nodes.status()}
        except Exception as exc:  # noqa: BLE001 - a git/IO failure, shown as is
            return _err(exc)
