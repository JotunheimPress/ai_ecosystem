"""One action at a time, with its output kept for anyone watching.

Actions are slow — `stack up` waits on health checks and a cold model load takes 20-30 s,
sometimes north of a minute — so they run in a thread and the browser watches over SSE
instead of holding a request open.

**A single slot, deliberately.** Concurrent `stack up` and `stack down backends` is a
race with a confusing outcome, and there is no legitimate reason to want two at once.
A second request while one is running is refused, not queued: a queued mutation that
fires minutes later, after you have changed your mind, is worse than an error.
"""
from __future__ import annotations

import contextvars
import itertools
import threading
import time
from dataclasses import dataclass, field

from services import stackctl

_counter = itertools.count(1)


@dataclass
class Job:
    id: str
    verb: str
    target: str | None
    started: float = field(default_factory=time.time)
    lines: list[str] = field(default_factory=list)
    exit_code: int | None = None
    error: str | None = None
    done: bool = False

    def public(self) -> dict:
        return {
            "id": self.id, "verb": self.verb, "target": self.target,
            "started": self.started, "done": self.done,
            "exit_code": self.exit_code, "error": self.error,
            "lines": list(self.lines),
            "label": f"stack {self.verb}{' ' + self.target if self.target else ''}",
        }


_lock = threading.Lock()
_current: Job | None = None
_last: Job | None = None


class Busy(RuntimeError):
    def __init__(self, job: Job):
        super().__init__(f"already running: {job.verb} {job.target or ''}".strip())
        self.job = job


def current() -> Job | None:
    return _current


def get(job_id: str) -> Job | None:
    for j in (_current, _last):
        if j and j.id == job_id:
            return j
    return None


def start(verb: str, target: str | None) -> Job:
    """Validate, then run in a thread. Raises StackError before starting on a bad request."""
    global _current, _last
    # Resolve first so an invalid verb/target is a plain error rather than a failed job —
    # the caller gets a 400, and the single slot is never consumed by something invalid.
    stackctl.resolve(verb, target)
    with _lock:
        if _current and not _current.done:
            raise Busy(_current)
        job = Job(id=f"{next(_counter)}-{int(time.time())}", verb=verb, target=target)
        _current = job

    def worker():
        global _last
        gen = None
        try:
            gen = stackctl.stream(verb, target)
            for kind, payload in gen:
                if kind == "line":
                    job.lines.append(payload)          # type: ignore[arg-type]
                else:
                    job.exit_code = payload            # type: ignore[assignment]
        except stackctl.StackError as e:
            job.error = str(e)
        except Exception as e:                          # noqa: BLE001 - surfaced to the UI
            job.error = f"{type(e).__name__}: {e}"
        finally:
            if gen is not None:
                gen.close()
            job.done = True
            _last = job
        # The registry can change as a result of what just ran (an app comes up), so
        # drop the cached app list rather than serving a stale one for up to a minute.
        stackctl.invalidate_apps()

    # In a copy of the request's context: the worker resolves again (stackctl.stream), and
    # whether the request came from the local network (stackctl.REQUEST_LAN) must hold there too.
    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(worker,), name=f"job-{job.id}", daemon=True).start()
    return job
