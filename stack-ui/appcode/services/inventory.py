"""What models are on disk, which one Kobold will boot, and (phase 2) changing that.

85 GB of weights had accumulated across the two backends with no inventory anywhere.
Phase 0 made that visible; phase 2 makes it manageable — select a Kobold model, and
delete files you no longer want.

Everything that writes lives at the bottom, behind `_safe_target()`. Two rules there:
a name is only accepted if it appears in the CURRENT listing (same "allowlist from live
state" idea as `stackctl.resolve`), and the resolved path's parent must be exactly the
directory we meant. Either check alone would probably do; deletion is worth both.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from services import provenance
from services.stackctl import ECO

KOBOLD_MODELS = ECO / "koboldcpp" / "models"
MODEL_ENV = ECO / "koboldcpp" / "model.env"
# Per-model layer counts. model.env holds only the ACTIVE selection, so without this,
# switching models loses the layer count you worked out for the previous one — and the
# right value differs per quant, which is exactly why it's worth remembering.
PRESETS = ECO / "koboldcpp" / "model-presets.json"
START_SH = ECO / "ai-stack" / "koboldcpp" / "start.sh"
# Where the backend router sends text (`stack route`); written only by `stack`.
ROUTE_FILE = ECO / "router" / "route.conf"
COMFY_MODELS = Path(os.environ.get("COMFYUI_STATE") or (ECO / "ComfyUI")) / "models"
# Fortuna: its model table ships in the recipe; its files and fortuna.env are state.
FORTUNA_TABLE = ECO / "ai-stack" / "fortuna" / "models.table"
FORTUNA_STATE = ECO / "fortuna"
FORTUNA_MODELS = FORTUNA_STATE / "models"
FORTUNA_ENV = FORTUNA_STATE / "fortuna.env"
HOST_ENV = ECO / "ai-stack" / "host.env"

# Subtrees worth showing separately. Anything else under models/ is rolled up as "other".
COMFY_KINDS = ["checkpoints", "loras", "vae", "diffusion_models", "text_encoders",
               "photomaker", "insightface", "unet", "clip"]

_CACHE: dict[str, tuple[float, object]] = {}
_TTL = 30.0  # the ComfyUI tree is ~70 GB; walk it at most twice a minute


def _cached(key: str, fn):
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def _tree_size(p: Path) -> tuple[int, int]:
    """(bytes, file count) — metadata only, never opens a file."""
    total = count = 0
    if not p.is_dir():
        return 0, 0
    for root, _dirs, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            try:
                total += os.stat(os.path.join(root, f)).st_size
                count += 1
            except OSError:
                pass
    return total, count


# The built-in default lives in start.sh, so read it from there rather than keeping a
# second copy of the filename here that could drift. This regex is a deliberate, narrow
# coupling to that one line; if it ever stops matching we report "unknown" rather than
# guessing, which is why the fallback is None and not a hardcoded name.
_DEFAULT = re.compile(r"KOBOLDCPP_MODEL:-\$\{FILE_MODEL:-([^}]+)\}")


def _builtin_default() -> str | None:
    try:
        m = _DEFAULT.search(START_SH.read_text())
        return m.group(1) if m else None
    except OSError:
        return None


def _read_model_env() -> dict[str, str]:
    """Same strict key-by-key parse as start.sh — never exec, never eval."""
    out: dict[str, str] = {}
    try:
        for line in MODEL_ENV.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() in ("KOBOLDCPP_MODEL", "KOBOLDCPP_GPU_LAYERS", "KOBOLDCPP_CONTEXT"):
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def read_presets() -> dict[str, int]:
    try:
        data = json.loads(PRESETS.read_text())
        return {k: int(v) for k, v in data.items() if isinstance(v, (int, str)) and str(v).isdigit()}
    except (OSError, ValueError, AttributeError):
        return {}


def write_preset(model: str, gpu_layers: int | None) -> None:
    """Remember (or forget) the layer count for one model. Atomic, like model.env."""
    presets = read_presets()
    if gpu_layers is None:
        presets.pop(model, None)
    else:
        presets[model] = int(gpu_layers)
    tmp = PRESETS.with_name(PRESETS.name + ".tmp")
    tmp.write_text(json.dumps(presets, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, PRESETS)


def text_route() -> str:
    """The router's text target, e.g. koboldcpp-local:5001. Read, never written, here."""
    try:
        m = re.search(r"set \$kobold_up\s+([^;\s]+)\s*;", ROUTE_FILE.read_text())
    except OSError:
        m = None
    return m.group(1) if m else "koboldcpp-local:5001"


def text_is_local() -> bool:
    return text_route().startswith("koboldcpp-local:")


def running_model() -> str | None:
    """What KoboldCPP has actually LOADED, straight from its own API.

    The selection on disk and the model in VRAM are different facts, and the gap between
    them is the whole reason a restart button exists. Best-effort: if it's down, that's
    not an error, it's just None.
    """
    try:
        with urllib.request.urlopen("http://localhost:5001/api/v1/model", timeout=2) as r:
            name = json.load(r).get("result") or ""
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    # Kobold reports "koboldcpp/<name>" with the extension stripped.
    return name.split("/", 1)[-1] or None


# Context windows start.sh accepts (KOBOLDCPP_CONTEXT); keep the two lists identical.
# 4K steps to 8K, then half-steps between powers of two: each size is 1.33–1.5x the last.
# 48K and up are for the AMD box's unified memory; the dropdown shows each one's cost.
CONTEXT_SIZES = (4096, 6144, 8192, 12288, 16384, 24576, 32768, 49152, 65536, 98304, 131072)
DEFAULT_CONTEXT = 8192          # KoboldCPP's own, used when model.env sets none


def running_context() -> int | None:
    """The context window KoboldCPP was started with, from its own API."""
    try:
        with urllib.request.urlopen(
                "http://localhost:5001/api/extra/true_max_context_length", timeout=2) as r:
            return int(json.load(r).get("value") or 0) or None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def set_kobold_context(size: int | None) -> dict:
    """Set KOBOLDCPP_CONTEXT in model.env (None = remove it: KoboldCPP's default).

    Every other line is kept exactly as it is. Like a model change, it applies on the
    next start and never restarts by itself.
    """
    if size is not None and size not in CONTEXT_SIZES:
        raise InventoryError(f"context must be one of {', '.join(map(str, CONTEXT_SIZES))}")
    try:
        lines = MODEL_ENV.read_text().splitlines()
    except FileNotFoundError:
        lines = ["# Written by stack-ui. Parsed (never sourced) by koboldcpp/start.sh."]
    lines = [ln for ln in lines if ln.partition("=")[0].strip() != "KOBOLDCPP_CONTEXT"]
    if size is not None:
        lines.append(f"KOBOLDCPP_CONTEXT={size}")
    tmp = MODEL_ENV.with_name(MODEL_ENV.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, MODEL_ENV)
    return kobold()


def kobold() -> dict:
    env = _read_model_env()
    selected = env.get("KOBOLDCPP_MODEL")
    active = selected or _builtin_default()
    files = []
    # Where each file came from, if this app is what downloaded it. One read for the whole
    # listing; a model with no record simply has url=None and the UI offers a search.
    sources = provenance.urls("kobold")
    if KOBOLD_MODELS.is_dir():
        for f in sorted(KOBOLD_MODELS.glob("*.gguf")):
            try:
                size = f.stat().st_size
            except OSError:
                continue
            files.append({"name": f.name, "size": size, "active": f.name == active,
                          "path": str(f), "url": sources.get(f.name)})
    running = running_model()
    ctx = env.get("KOBOLDCPP_CONTEXT")
    context = int(ctx) if ctx and ctx.isdigit() else None
    running_ctx = running_context() if running else None
    # localhost:5001 is the router. With text routed to another machine, `running` is THAT
    # machine's model: comparing it with this machine's selection would offer a restart of
    # the local copy to fix a difference the restart can't touch.
    local = text_is_local()
    model_pending = local and bool(running) and bool(active) and Path(active).stem != running
    context_pending = local and bool(running_ctx) and running_ctx != (context or DEFAULT_CONTEXT)
    for f in files:
        # Kobold strips the .gguf when reporting, so compare on the stem.
        f["running"] = local and bool(running) and Path(f["name"]).stem == running
    return {
        "dir": str(KOBOLD_MODELS),
        "models": files,
        "active": active,
        # Where the selection comes from matters: "default" means it is not pinned and a
        # future `stack up` on another machine would boot something else.
        "source": "model.env" if selected else ("start.sh default" if active else "unknown"),
        "gpu_layers": env.get("KOBOLDCPP_GPU_LAYERS"),
        # An active model that isn't on disk is a start.sh hard failure, so surface it here.
        "active_present": any(f["active"] for f in files) if active else False,
        "running": running,
        "text_local": local,
        "text_route": text_route(),
        "presets": read_presets(),
        # The selection only reaches VRAM on a restart; the UI needs to say so plainly
        # rather than showing a green tick over a model that isn't loaded.
        "restart_needed": model_pending or context_pending,
        "restart_for": [w for w, on in (("model", model_pending), ("context", context_pending)) if on],
        # Context window: what model.env asks for (None = KoboldCPP's default) and what
        # the running server actually has.
        "context": context,
        "context_default": DEFAULT_CONTEXT,
        "context_sizes": list(CONTEXT_SIZES),
        "running_context": running_ctx,
    }


# --- Fortuna -------------------------------------------------------------------
# The files Fortuna can serve are a fixed table (fortuna/models.table), each pinned by sha256
# to one Hub commit. start.sh reads the same table, so a name valid here is valid there.
_ROW = re.compile(r"^[a-z0-9][a-z0-9._-]*\|")


def fortuna_table() -> dict:
    """{repo, rev, models: [{name, file, sha256, bytes, temperature, knockout, default_for}]}."""
    out: dict = {"repo": None, "rev": None, "models": []}
    try:
        text = FORTUNA_TABLE.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        if line.startswith("REPO="):
            out["repo"] = line[5:].strip()
        elif line.startswith("REV="):
            out["rev"] = line[4:].strip()
        elif _ROW.match(line):
            f = line.split("|")
            if len(f) != 7 or not f[3].isdigit():
                continue
            out["models"].append({
                "name": f[0], "file": f[1], "sha256": f[2], "bytes": int(f[3]),
                "temperature": f[4], "knockout": f[5],
                "default_for": [g for g in f[6].split(",") if g and g != "-"]})
    return out


def host_gpu() -> str:
    """nvidia | amd | none — as lib/host.sh resolves HOST_GPU: STACK_GPU (run-podman.sh sets
    it in the container), then host.env's GPU=, then detection. Detection here is coarser than
    the script's (no nvidia-ctk): a CDI spec means NVIDIA, else /dev/kfd means AMD."""
    v = os.environ.get("STACK_GPU", "")
    if not v:
        try:
            for line in HOST_ENV.read_text().splitlines():
                k, _, val = line.partition("=")
                if k.strip() == "GPU":
                    v = val.strip().strip('"').strip("'")
        except OSError:
            pass
    if v in ("nvidia", "amd", "none"):
        return v
    if Path("/etc/cdi/nvidia.yaml").exists() or Path("/var/run/cdi/nvidia.yaml").exists():
        return "nvidia"
    return "amd" if Path("/dev/kfd").exists() else "none"


def _read_fortuna_env() -> dict[str, str]:
    """Same strict read as start.sh's _env_get: KEY=value, last one wins, never executed."""
    out: dict[str, str] = {}
    try:
        for line in FORTUNA_ENV.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip():
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def running_fortuna() -> str | None:
    """The model Fortuna has loaded, from its /health (200 only once loaded). Via the router,
    which always forwards decisions to this machine's fortuna-local."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8009/health", timeout=2) as r:
            name = json.load(r).get("model")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return name if isinstance(name, str) and name else None


def _stamp_matches(stamp: Path, f: Path, sha: str) -> bool:
    """start.sh's stamp: "<size> <mtime> <sha256>" of the file as it was hashed."""
    try:
        st = f.stat()
        return stamp.read_text().strip() == f"{st.st_size} {int(st.st_mtime)} {sha}"
    except OSError:
        return False


def _integrity(m: dict) -> str:
    """verified | mismatch | unchecked | partial | absent. Never hashes: it reads the stamps
    start.sh writes (`--verify`, `--fetch`, or its first start with the file)."""
    f = FORTUNA_MODELS / m["file"]
    if not f.is_file():
        return "partial" if (FORTUNA_MODELS / (m["file"] + ".part")).is_file() else "absent"
    if _stamp_matches(f.with_name(f.name + ".verified"), f, m["sha256"]):
        return "verified"
    if _stamp_matches(f.with_name(f.name + ".mismatch"), f, m["sha256"]):
        return "mismatch"
    return "unchecked"


_PROBE = object()


def fortuna_models(running=_PROBE) -> dict:
    """Fortuna's known files with their integrity, which one boots, and which one is loaded.
    ``running`` skips the /health probe when the caller already has the answer."""
    t = fortuna_table()
    gpu = host_gpu()
    env = _read_fortuna_env()
    default = next((m["name"] for m in t["models"] if gpu in m["default_for"]), None)
    selected = env.get("MODEL") or default
    if running is _PROBE:
        running = running_fortuna()
    rows = []
    for m in t["models"]:
        state = _integrity(m)
        part = FORTUNA_MODELS / (m["file"] + ".part")
        rows.append({
            **m, "integrity": state,
            "present": state not in ("absent", "partial"),
            "part_bytes": part.stat().st_size if state == "partial" else 0,
            "selected": m["name"] == selected, "running": m["name"] == running,
            "default": m["name"] == default,
            "url": (f"https://huggingface.co/{t['repo']}/blob/{t['rev']}/{m['file']}"
                    if t["repo"] and t["rev"] else None)})
    sel = next((r for r in rows if r["selected"]), None)
    return {
        "dir": str(FORTUNA_MODELS), "repo": t["repo"], "rev": t["rev"], "gpu": gpu,
        "models": rows, "selected": selected, "default": default,
        "source": "fortuna.env" if env.get("MODEL") else (f"default for {gpu}" if default else "unknown"),
        # A MODEL= the table doesn't know: start.sh refuses to start with it.
        "selected_known": sel is not None,
        "selected_integrity": sel["integrity"] if sel else None,
        "running": running,
        "restart_needed": bool(running) and bool(selected) and running != selected,
    }


def comfyui() -> dict:
    def build():
        kinds, seen = [], set()
        for kind in COMFY_KINDS:
            p = COMFY_MODELS / kind
            if not p.is_dir():
                continue
            size, count = _tree_size(p)
            seen.add(kind)
            kinds.append({"kind": kind, "size": size, "count": count})
        other_size = other_count = 0
        if COMFY_MODELS.is_dir():
            for p in COMFY_MODELS.iterdir():
                if p.is_dir() and p.name not in seen:
                    s, c = _tree_size(p)
                    other_size += s
                    other_count += c
        if other_count:
            kinds.append({"kind": "other", "size": other_size, "count": other_count})
        return {"dir": str(COMFY_MODELS), "kinds": kinds,
                "total": sum(k["size"] for k in kinds)}
    return _cached("comfyui", build)


def comfy_files(kind: str) -> list[dict]:
    """Individual files of one writable ComfyUI collection (see IMAGE_KINDS).

    Top level only: the write path (`_safe_target`) refuses names with separators, so a
    file in a subfolder could be listed but never deleted — better not to offer it. The
    per-kind totals in `comfyui()` still count subfolders.
    """
    if kind not in WRITABLE or kind == "kobold":
        raise InventoryError(f"unknown collection: {kind!r}")

    def build():
        p, exts = WRITABLE[kind]
        sources = provenance.urls(kind)
        out = []
        if p.is_dir():
            for f in sorted(p.iterdir()):
                if f.suffix in exts and f.is_file():
                    try:
                        out.append({"name": f.name, "size": f.stat().st_size, "kind": kind,
                                    "url": sources.get(f.name)})
                    except OSError:
                        pass
        return out
    return _cached(f"files:{kind}", build)


def checkpoints() -> list[dict]:
    """Individual checkpoint files — the ones you pick between in the other frontends."""
    return comfy_files("checkpoint")


# =============================================================================
# Writes. Everything above this line only reads.
# =============================================================================

GGUF_EXTS = (".gguf",)
CKPT_EXTS = (".safetensors", ".ckpt")

# Which named collections may be written to, and what belongs in them. A request names a
# KEY here, never a path — the same shape as stackctl's verb table, for the same reason.
WRITABLE: dict[str, tuple[Path, tuple[str, ...]]] = {
    "kobold": (KOBOLD_MODELS, GGUF_EXTS),
    "checkpoint": (COMFY_MODELS / "checkpoints", CKPT_EXTS),
    "lora": (COMFY_MODELS / "loras", CKPT_EXTS),
    "vae": (COMFY_MODELS / "vae", CKPT_EXTS),
    # Split models (Flux, SD3.5, Wan…): ComfyUI loads the diffusion model, its text
    # encoders and the VAE from separate folders. Current names, not the legacy
    # unet/ and clip/ aliases, which ComfyUI still reads but new guides don't use.
    "diffusion_model": (COMFY_MODELS / "diffusion_models", CKPT_EXTS),
    "text_encoder": (COMFY_MODELS / "text_encoders", CKPT_EXTS),
    # Fortuna's files: only deleted from here (they are fetched by start.sh, never downloads.py,
    # and selected by table name in select_fortuna).
    "fortuna": (FORTUNA_MODELS, GGUF_EXTS),
}
# The image-side collections, in display order.
IMAGE_KINDS = ("checkpoint", "lora", "vae", "diffusion_model", "text_encoder")

MAX_GPU_LAYERS = 200      # far above any real model; catches typos and nonsense, not use


class InventoryError(RuntimeError):
    pass


def _safe_target(kind: str, name: str) -> Path:
    """Resolve (kind, name) to a real file, or raise. The only way to name a file here."""
    entry = WRITABLE.get(kind)
    if entry is None:
        raise InventoryError(f"unknown collection: {kind!r}")
    directory, exts = entry

    # Reject before touching the filesystem: no separators, no traversal, no dotfiles.
    if not name or name != Path(name).name or name.startswith("."):
        raise InventoryError(f"invalid name: {name!r}")
    if not name.endswith(exts):
        raise InventoryError(f"{name!r} is not a {'/'.join(exts)} file")

    p = directory / name
    # Belt and braces: after resolution the parent must still be the directory we meant,
    # so a symlinked models dir or a crafted name can't land the write elsewhere.
    if p.parent.resolve() != directory.resolve():
        raise InventoryError(f"refusing to touch a path outside {directory}")
    if not p.is_file():
        raise InventoryError(f"no such file: {name}")
    return p


# model.env keys stack-ui doesn't write but must not drop when it rewrites the file.
_KEPT_KEYS = ("KOBOLDCPP_CONTEXT",)   # not the adapter: it belongs to the model


def _kept_model_env() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = MODEL_ENV.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        k, v = k.strip(), v.strip()
        # Same character rule as the written values: one line, no quotes or shell syntax.
        if sep and k in _KEPT_KEYS and v and all(c.isalnum() or c in "._-" for c in v):
            out[k] = v
    return out


def select_kobold(model: str, gpu_layers: int | None) -> dict:
    """Pin the model KoboldCPP boots, by writing koboldcpp/model.env.

    Takes effect on the next start — the caller is expected to follow with a restart, and
    the UI says so. Written atomically: a half-written model.env would be parsed by
    start.sh on the next boot, and a truncated model name fails the start outright.
    """
    _safe_target("kobold", model)          # must be a real .gguf that is actually there
    if gpu_layers is not None:
        if not isinstance(gpu_layers, int) or not (0 <= gpu_layers <= MAX_GPU_LAYERS):
            raise InventoryError(f"gpu_layers must be 0–{MAX_GPU_LAYERS}")

    # An explicit value is remembered for this model; without one, a previously
    # remembered value is reused rather than silently dropping back to the default.
    if gpu_layers is not None:
        write_preset(model, gpu_layers)
    else:
        gpu_layers = read_presets().get(model)

    lines = [
        "# Written by stack-ui. Parsed (never sourced) by koboldcpp/start.sh.",
        f"KOBOLDCPP_MODEL={model}",
    ]
    if gpu_layers is not None:
        lines.append(f"KOBOLDCPP_GPU_LAYERS={gpu_layers}")
    # Settings this screen doesn't edit (set by hand) survive a model change.
    lines += [f"{k}={v}" for k, v in _kept_model_env().items()]

    tmp = MODEL_ENV.with_name(MODEL_ENV.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, MODEL_ENV)             # atomic within the same directory
    return kobold()


def select_fortuna(name: str) -> dict:
    """Pin the model Fortuna boots: MODEL= in fortuna.env. Applies on its next start.

    Only a name from the table, and only a file that is downloaded and not known to be bad —
    start.sh would refuse anything else at the next start, which may be a boot. Every other
    line of fortuna.env (API_KEY…) is kept as it is. Written atomically, like model.env.
    """
    f = fortuna_models()
    row = next((m for m in f["models"] if m["name"] == name), None)
    if row is None:
        raise InventoryError(f"unknown Fortuna model: {name!r}")
    if not row["present"]:
        raise InventoryError(f"{name} is not downloaded — download it first")
    if row["integrity"] == "mismatch":
        raise InventoryError(f"{name} failed its sha256 check — delete it and download it again")
    try:
        lines = FORTUNA_ENV.read_text().splitlines()
    except FileNotFoundError:
        lines = ["# Parsed (never sourced) by ai-stack/fortuna/start.sh. MODEL= written by stack-ui."]
    lines = [ln for ln in lines if ln.partition("=")[0].strip() != "MODEL"]
    lines.append(f"MODEL={name}")
    FORTUNA_STATE.mkdir(parents=True, exist_ok=True)
    tmp = FORTUNA_ENV.with_name(FORTUNA_ENV.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, FORTUNA_ENV)
    return fortuna_models()


def delete_model(kind: str, name: str) -> dict:
    """Delete one model file. Irreversible, so it refuses the two obvious footguns."""
    p = _safe_target(kind, name)

    if kind == "kobold":
        state = kobold()
        if state["active"] == name:
            # start.sh hard-fails on a missing selected model, so this would break the
            # backend at the next start rather than at the moment of deletion.
            raise InventoryError(
                f"{name} is the selected model — choose another one first")
        if any(m["name"] == name and m["running"] for m in state["models"]):
            raise InventoryError(f"{name} is currently loaded — stop koboldcpp first")

    if kind == "fortuna":
        f = fortuna_models()
        row = next((m for m in f["models"] if m["file"] == name), None)
        if row and row["selected"]:
            raise InventoryError(f"{name} is the model Fortuna boots — choose another one first")
        if row and row["running"]:
            raise InventoryError(f"{name} is loaded in Fortuna — stop fortuna first")

    size = p.stat().st_size
    p.unlink()
    if kind == "fortuna":
        # Its integrity stamps describe a file that is gone.
        for ext in (".verified", ".mismatch"):
            p.with_name(p.name + ext).unlink(missing_ok=True)
    # Drop the provenance entry too, or a later file of the same name inherits a link to
    # something it isn't.
    provenance.forget(kind, name)
    _CACHE.clear()                          # the ComfyUI walk is cached; it just changed
    return {"deleted": name, "kind": kind, "freed": size}


def safe_new_target(kind: str, name: str) -> Path:
    """Resolve (kind, name) for a file that does NOT exist yet — the download destination.

    Same checks as `_safe_target` minus "must exist", plus a refusal to overwrite. A
    download that silently replaced a model you already had would be the worst kind of
    surprise: the file changes under a backend that may have it loaded.
    """
    entry = WRITABLE.get(kind)
    if entry is None:
        raise InventoryError(f"unknown collection: {kind!r}")
    directory, exts = entry
    if not name or name != Path(name).name or name.startswith("."):
        raise InventoryError(f"invalid name: {name!r}")
    if not name.endswith(exts):
        raise InventoryError(f"{name!r} is not a {'/'.join(exts)} file")
    p = directory / name
    if p.parent.resolve() != directory.resolve():
        raise InventoryError(f"refusing to write outside {directory}")
    if p.exists():
        raise InventoryError(f"{name} already exists — delete it first")
    directory.mkdir(parents=True, exist_ok=True)
    return p


def invalidate() -> None:
    """Drop the size caches — call after anything that changes what's on disk."""
    _CACHE.clear()
