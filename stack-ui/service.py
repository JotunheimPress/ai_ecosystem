#!/usr/bin/env python3
"""AI Ecosystem (stack-ui) — ASGI entrypoint. pytincture + wapyt since 2026-10-26.

    python service.py            # what the container runs

The page is Python in the browser (``appcode/stackui.py``, Pyodide + wapyt). It talks to
the server two ways:

- **BFF calls** (``services/stack_api.py``, ``services/models_api.py``) for everything
  request/response, mutations included. pytincture refuses a BFF write that is not
  ``application/json`` from the same origin — the same guarantee the old required
  ``X-Stack-UI`` header gave: a cross-origin form cannot send it, and anything that can is
  forced through a CORS preflight nobody answers.
- **Plain GET event streams** below, for the three things that outlive a request: an
  action's output (``stack up`` can take minutes), a container's log, a download's
  progress. They only read; closing one kills what it follows.

**Any device on the LAN, no auth.** The stack is a local product for whoever installs it, so
the dashboard answers on every interface like the apps do (``STACK_UI_HOST``, default
``0.0.0.0``). pytincture still refuses a BFF write that isn't same-origin JSON, so another
web page open in the browser can't drive it.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
APPCODE = HERE / "appcode"
# BFF modules import `services.<name>`; pytincture loads them by path from appcode, so
# appcode must be importable here — and it must be the SAME `services` package these
# stream routes import, or a job started by a BFF call would be invisible to its stream.
sys.path.insert(0, str(APPCODE))

from fastapi import HTTPException  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pytincture import PytinctureConfig, create_app  # noqa: E402

from services import downloads, jobs, stackctl  # noqa: E402

APPLICATION = "stackui"
HOST = os.environ.get("STACK_UI_HOST", "0.0.0.0")
PORT = int(os.environ.get("STACK_UI_PORT", "7863"))


def build_app():
    application = create_app(
        PytinctureConfig(
            modules_path=str(APPCODE),
            default_application=APPLICATION,
            cookie_namespace="stackui",
        )
    )

    class MarkLanRequests:
        """Tags every request with whether it comes from the local network
        (stackctl.REQUEST_LAN), for the few things nothing else may do. Plain ASGI, not @middleware("http"), so the
        value is set in the very context the route and its BFF thread run in."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            token = stackctl.REQUEST_LAN.set(stackctl.is_lan_request(scope))
            try:
                await self.app(scope, receive, send)
            finally:
                stackctl.REQUEST_LAN.reset(token)

    application.add_middleware(MarkLanRequests)

    # Same-origin stylesheet; pytincture's CSP allows styles from 'self'.
    application.mount("/static", StaticFiles(directory=str(APPCODE / "static")),
                      name="static")

    @application.middleware("http")
    async def revalidate_static(request, call_next):
        # StaticFiles sends an ETag but no Cache-Control, which lets a browser reuse a stale
        # stylesheet after a rebuild without asking. no-cache = always revalidate (a 304
        # when unchanged), so a new image's CSS shows up on the next load.
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def sse(gen: Iterator[dict]) -> StreamingResponse:
        def encode():
            for event in gen:
                yield f"data: {json.dumps(event)}\n\n"
        return StreamingResponse(encode(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @application.get("/api/health")
    def health():
        # `stack status` probes this; keep it cheap and dependency-free.
        return {"ok": True}

    @application.get("/api/action/{job_id}/events")
    def action_events(job_id: str):
        """Replay what the job has printed so far, then follow it to completion.

        Polls the job's line list rather than a queue, so a reload or a second tab picks
        the stream up from the beginning instead of joining mid-flight.
        """
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")

        def gen():
            sent = 0
            while True:
                while sent < len(job.lines):
                    yield {"type": "line", "text": job.lines[sent]}
                    sent += 1
                if job.done:
                    yield {"type": "done", "exit_code": job.exit_code, "error": job.error}
                    return
                time.sleep(0.2)
        return sse(gen())

    @application.get("/api/logs/{name}/events")
    def logs(name: str):
        """Follow one app's container log (`stack logs <app>` → `podman logs -f`).

        Closing the stream closes this generator, which kills the process group — that
        matters, because `podman logs -f` never exits on its own.
        """
        # Validate BEFORE building the generator: stackctl.stream() is a generator
        # function, so its own target check would only run after the 200 has gone out.
        try:
            stackctl.resolve("logs", name)
        except stackctl.StackError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        stream = stackctl.stream("logs", name)

        def gen():
            try:
                for kind, payload in stream:
                    if kind == "line":
                        yield {"type": "line", "text": payload}
                    else:
                        yield {"type": "done", "exit_code": payload}
            finally:
                stream.close()
        return sse(gen())

    @application.get("/api/downloads/{job_id}/events")
    def download_events(job_id: str):
        d = downloads.get(job_id)
        if d is None:
            raise HTTPException(status_code=404, detail="no such download")

        def gen():
            while True:
                yield {"type": "progress", **d.public()}
                if d.state != "running":
                    return
                time.sleep(1)
        return sse(gen())

    return application


app = build_app()


if __name__ == "__main__":
    import uvicorn

    print(f"AI Ecosystem on http://{'127.0.0.1' if HOST in ('0.0.0.0', 'localhost') else HOST}:{PORT}/",
          flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
