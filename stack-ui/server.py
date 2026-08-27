"""stack-ui — control dashboard for the AI stack (phases 0-3).

Runs as a container like everything else in the stack, but an unusual one: it drives the
HOST's podman engine (mounted socket) and the HOST's user systemd (mounted session bus,
via the systemctl shim). See run-podman.sh for why each mount is load-bearing.

Phase 1 adds mutating verbs (on/off/up/down/restart/start) and streamed logs. Everything
that changes state goes through services/stackctl.VERBS — a fixed verb->argv table — with
targets validated against the live registry. There is no free-text command path.

Bound to 127.0.0.1 (see run.py). It has no auth, which is only acceptable on loopback —
hence the Host/Origin guards below, which matter now that POST does something.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterator

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from services import civitai, downloads, hub, inventory, jobs, nodes, stackctl, vram

app = FastAPI(title="AI Ecosystem", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

ALLOWED_HOSTS = {"localhost:7863", "127.0.0.1:7863", "[::1]:7863"}
ALLOWED_ORIGINS = {"http://localhost:7863", "http://127.0.0.1:7863", "http://[::1]:7863"}


@app.middleware("http")
async def guard(request: Request, call_next):
    """Cheap defences that a no-auth loopback service still needs.

    Without auth, the browser is the confused deputy: any page you happen to have open
    can POST to http://localhost:7863 and stop your stack. Two mitigations, both free:

    - **Host check** — blocks DNS rebinding, where an attacker's domain resolves to
      127.0.0.1 and their script then talks to us as a same-origin peer.
    - **Origin check + required custom header on writes** — a cross-origin form POST
      can't set a custom header, and anything that can set one is forced through a
      CORS preflight we never answer.

    None of this is a substitute for auth. It's what makes loopback-only defensible.
    """
    host = (request.headers.get("host") or "").lower()
    if host and host not in ALLOWED_HOSTS:
        return JSONResponse(status_code=421, content={"detail": f"unexpected Host: {host}"})

    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.lower() not in ALLOWED_ORIGINS:
            return JSONResponse(status_code=403, content={"detail": "cross-origin write refused"})
        if request.headers.get("x-stack-ui") != "1":
            return JSONResponse(status_code=403,
                                content={"detail": "missing X-Stack-UI header"})
    return await call_next(request)


def _sse(gen: Iterator[dict]) -> StreamingResponse:
    def encode():
        for event in gen:
            yield f"data: {json.dumps(event)}\n\n"
    return StreamingResponse(encode(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


# --- read-only ---------------------------------------------------------------
@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    try:
        res = stackctl.run("status")
    except stackctl.StackError as e:
        raise HTTPException(status_code=503, detail=str(e))
    job = jobs.current()
    return {**stackctl.parse_status(res["output"]), "raw": res["output"],
            "duration_ms": res["duration_ms"],
            "busy": bool(job and not job.done),
            "job": job.public() if job and not job.done else None}


@app.get("/api/doctor")
def doctor():
    """Not polled — it shells out to podman several times. On demand only."""
    try:
        res = stackctl.run("doctor")
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


# --- inventory writes (phase 2) ----------------------------------------------
@app.post("/api/kobold/select")
def kobold_select(body: dict = Body(...)):
    """Pin the model KoboldCPP boots. Takes effect on its next start, not now.

    Deliberately does NOT restart on your behalf: a restart drops whatever is loaded and
    costs 20-30 s of model load, and doing that as a side effect of clicking a name in a
    list is the kind of surprise this UI shouldn't spring. The response says a restart is
    pending; the button that does it is the ordinary `restart koboldcpp` action.
    """
    layers = body.get("gpu_layers")
    if isinstance(layers, str) and layers.strip():
        try:
            layers = int(layers)
        except ValueError:
            raise HTTPException(status_code=400, detail="gpu_layers must be a number")
    elif layers == "" or layers is None:
        layers = None
    try:
        return inventory.select_kobold(body.get("model") or "", layers)
    except inventory.InventoryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/models/{kind}/{name}")
def delete_model(kind: str, name: str):
    """Delete one model file. The name is validated against the live listing, not trusted."""
    try:
        return inventory.delete_model(kind, name)
    except inventory.InventoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"delete failed: {e}")


# --- ComfyUI custom nodes ----------------------------------------------------
@app.get("/api/nodes")
def custom_nodes():
    """Pinned commits vs what's checked out. Read from `stack`'s own registry, so this
    can't disagree with `stack doctor` about which commit is correct."""
    return nodes.status()


# --- VRAM planner ------------------------------------------------------------
@app.get("/api/vram")
def vram_stats():
    """Live VRAM plus what each backend would need. Figures come from ComfyUI — there is
    no NVIDIA tooling in this container — so they're unavailable while it's stopped."""
    s = vram.stats()
    k = inventory.kobold()
    models = []
    for m in k["models"]:
        meta = vram.gguf_meta(Path(m["path"]))
        models.append({**m, "arch": meta.get("arch"), "n_layers": meta.get("n_layers")})
    return {"vram": s, "kobold": {**k, "models": models},
            "checkpoints": inventory.checkpoints()}


@app.post("/api/vram/free")
def vram_free():
    """Make ComfyUI release VRAM it is holding after a job.

    This is the fix for the failure that reads as KoboldCPP being broken: ComfyUI keeps
    ~6.4 GB resident, Kobold OOMs at startup, and its --rm container vanishes before it
    logs anything. No restart needed.
    """
    try:
        return vram.free_vram()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/vram/plan")
def vram_plan(model: str, checkpoint: str = ""):
    """How many layers of `model` fit alongside `checkpoint`. An estimate, labelled so."""
    try:
        path = inventory._safe_target("kobold", model)
    except inventory.InventoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ckpt_size = 0
    if checkpoint:
        try:
            ckpt_size = inventory._safe_target("checkpoint", checkpoint).stat().st_size
        except (inventory.InventoryError, OSError) as e:
            raise HTTPException(status_code=400, detail=str(e))
    s = vram.stats()
    return vram.plan(path, path.stat().st_size, ckpt_size, s.get("total") or 0)


# --- model browsing + downloads (phase 3) ------------------------------------
@app.get("/api/hub/search")
def hub_search(q: str, kind: str = "gguf", limit: int = 25):
    try:
        return {"results": hub.search(q, kind=kind, limit=limit)}
    except hub.HubError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/hub/files")
def hub_files(repo: str, kind: str = "kobold"):
    """A repo's downloadable files with sizes, each carrying a fit hint.

    The hints are advisory and labelled as such: VRAM comes from ComfyUI's /system_stats
    (there is no NVIDIA tooling in this container), and is suppressed entirely rather than
    guessed when that's unavailable.
    """
    exts = (".gguf",) if kind == "kobold" else (".safetensors", ".ckpt")
    try:
        data = hub.files(repo, exts)
    except hub.HubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    vram = hub.vram_total()
    free = stackctl.disk()["free"]
    for f in data["files"]:
        f["fit"] = hub.fit(f["size"], vram, free)
    data["vram_total"] = vram
    data["free_disk"] = free
    return data


@app.get("/api/civitai/search")
def civitai_search(q: str, type: str = "Checkpoint", base: str = "", limit: int = 20):
    """Civitai keeps model -> version -> file. The nesting is preserved: picking the
    version (SD 1.5 vs SDXL, v1.8 vs v2) is the actual decision."""
    try:
        items = civitai.search(q, model_type=type, base=base, limit=limit)
    except civitai.CivitaiError as e:
        raise HTTPException(status_code=502, detail=str(e))
    free = stackctl.disk()["free"]
    for m in items:
        for v in m["versions"]:
            for f in v["files"]:
                f["disk_ok"] = free > f["size"] * 1.05
    return {"results": items, "free_disk": free}


@app.post("/api/downloads")
def download_start(body: dict = Body(...)):
    source = str(body.get("source") or "hf")
    kind = str(body.get("kind") or "kobold")
    activate = bool(body.get("activate"))
    try:
        if source == "civitai":
            d = downloads.start_civitai(body.get("version_id"), str(body.get("name") or ""),
                                        kind, activate)
        elif source == "hf":
            d = downloads.start_hf(str(body.get("repo") or ""), str(body.get("path") or ""),
                                   kind, activate)
        else:
            raise HTTPException(status_code=400, detail=f"unknown source: {source!r}")
    except downloads.Busy as e:
        raise HTTPException(status_code=409, detail=str(e))
    except inventory.InventoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(status_code=202, content=d.public())


@app.get("/api/downloads")
def download_state():
    d = downloads.current()
    return {"download": d.public() if d else None}


@app.get("/api/downloads/{job_id}/events")
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
    return _sse(gen())


@app.delete("/api/downloads/{job_id}")
def download_cancel(job_id: str):
    if not downloads.cancel(job_id):
        raise HTTPException(status_code=404, detail="not running")
    return {"cancelled": job_id}


# --- actions -----------------------------------------------------------------
@app.post("/api/action")
def action(body: dict = Body(...)):
    """Start one stack action. Returns immediately; watch it over SSE."""
    verb = body.get("verb")
    target = body.get("target") or None
    v = stackctl.VERBS.get(verb or "")
    if v is None or not v.mutating:
        # Read-only verbs have their own routes; refusing them here keeps this endpoint
        # unambiguously "the thing that changes state".
        raise HTTPException(status_code=400, detail=f"not an action verb: {verb!r}")
    try:
        job = jobs.start(verb, target)
    except jobs.Busy as e:
        raise HTTPException(status_code=409, detail=str(e))
    except stackctl.StackError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(status_code=202, content=job.public())


@app.get("/api/action")
def action_state():
    job = jobs.current()
    return {"job": job.public() if job else None}


@app.get("/api/action/{job_id}/events")
def action_events(job_id: str):
    """Replay what the job has printed so far, then follow it to completion.

    Polls the job's line list rather than using a queue, so a reload or a second tab
    picks the stream up from the beginning instead of joining mid-flight.
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
    return _sse(gen())


@app.get("/api/logs/{name}/events")
def logs(name: str):
    """Follow one app's container log (`stack logs <app>` → `podman logs -f`).

    Closing the browser tab closes this generator, which kills the process group — that
    matters here, because `podman logs -f` never exits on its own.
    """
    # Validate BEFORE building the generator. stackctl.stream() is a generator function,
    # so its body — including the target check — doesn't run until the first iteration,
    # by which point the 200 and the SSE headers have already gone out. Calling resolve()
    # here is what turns a bad app name back into a 400 instead of an empty 200 stream.
    try:
        stackctl.resolve("logs", name)
    except stackctl.StackError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
    return _sse(gen())
