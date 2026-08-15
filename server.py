"""stack-ui — read-only control dashboard for the AI stack (phase 0).

Runs on the HOST, not in a container: `stack` drives podman *and* systemctl --user, and
the on/off switch is exactly what containerization breaks. It fills the kind=proc slot
the registry has been holding open.

Phase 0 is read-only by construction — services/stackctl.py allowlists two verbs, both
of which only report. There is no endpoint here that changes the state of the machine.

Bound to 127.0.0.1 (see run.py). It has no auth, and that is only acceptable while it
stays on loopback.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from services import inventory, stackctl

app = FastAPI(title="stack-ui", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/health")
def health():
    """Cheap liveness probe — this is the URL `stack` polls for this app."""
    return {"ok": True}


@app.get("/api/status")
def status():
    try:
        res = stackctl.run("status")
    except stackctl.StackError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {**stackctl.parse_status(res["output"]), "raw": res["output"],
            "duration_ms": res["duration_ms"]}


@app.get("/api/doctor")
def doctor():
    """Deliberately not polled — it shells out to podman several times. On demand only."""
    try:
        res = stackctl.run("doctor", timeout=60)
    except stackctl.StackError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {**stackctl.parse_doctor(res["output"]), "raw": res["output"],
            "duration_ms": res["duration_ms"]}


@app.get("/api/inventory")
def inv():
    return {
        "kobold": inventory.kobold(),
        "comfyui": inventory.comfyui(),
        "checkpoints": inventory.checkpoints(),
        "disk": stackctl.disk(),
    }


@app.exception_handler(stackctl.StackError)
def _stack_error(_request: Request, exc: stackctl.StackError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})
