"""Civitai search — where the ComfyUI checkpoints in comfyui/INSTALL.md §8 actually come from.

Same shape as `hub.py`: stdlib only, our own UI over their JSON API. Search needs no key;
many *downloads* do, so `CIVITAI_TOKEN` is picked up when set and sent as a bearer header —
never as a query parameter, which would land in logs and history.

Civitai's model → version → file nesting is deeper than HuggingFace's flat file list, and
the download unit is a **version's file**, not a model. That shape is preserved here rather
than flattened, because picking the version (SD 1.5 vs SDXL, v1.8 vs v2) is the actual
decision — flattening it would hide the thing you came to choose.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

API = "https://civitai.com/api/v1"
UA = "stack-ui/0.4 (+local control dashboard)"
TIMEOUT = 20

# Civitai's model types -> our destination collections (inventory.WRITABLE keys).
TYPES = {"Checkpoint": "checkpoint", "LORA": "lora", "VAE": "vae"}
MODEL_EXTS = (".safetensors", ".ckpt", ".pt")


class CivitaiError(RuntimeError):
    pass


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    token = os.environ.get("CIVITAI_TOKEN", "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise CivitaiError("access denied — this needs CIVITAI_TOKEN set")
        if e.code == 429:
            raise CivitaiError("rate-limited by civitai — wait a moment")
        # Their error bodies are often the useful part ("Model search is temporarily
        # overloaded — please retry"), and a bare status code sends you debugging your own
        # client instead of reading their message. Observed live during development.
        detail = ""
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
            detail = body.get("error") or body.get("message") or ""
        except Exception:                                # noqa: BLE001 - best effort
            pass
        raise CivitaiError(f"civitai returned {e.code}" + (f": {detail}" if detail else ""))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CivitaiError(f"cannot reach civitai: {e}")
    except ValueError:
        raise CivitaiError("civitai returned something that isn't JSON")


def _files(version: dict) -> list[dict]:
    out = []
    for f in version.get("files") or []:
        name = f.get("name") or ""
        if not name.endswith(MODEL_EXTS):
            continue
        out.append({
            "name": name,
            # sizeKB is kilobytes and a float; bytes is what everything else here speaks.
            "size": int((f.get("sizeKB") or 0) * 1024),
            "primary": bool(f.get("primary")),
            "format": (f.get("metadata") or {}).get("format"),
        })
    out.sort(key=lambda f: (not f["primary"], f["name"]))
    return out


def search(query: str, model_type: str = "Checkpoint", base: str = "", limit: int = 20) -> list[dict]:
    if model_type not in TYPES:
        raise CivitaiError(f"unsupported type: {model_type!r}")
    q = {"query": query, "types": model_type, "limit": max(1, min(limit, 50)),
         "sort": "Most Downloaded"}
    if base:
        q["baseModels"] = base
    data = _get(f"{API}/models?{urllib.parse.urlencode(q)}")
    items = []
    for m in data.get("items") or []:
        versions = []
        for v in (m.get("modelVersions") or [])[:6]:      # newest few; the rest is noise
            files = _files(v)
            if files:
                versions.append({"id": v.get("id"), "name": v.get("name"),
                                 "base": v.get("baseModel"), "files": files})
        if not versions:
            continue
        items.append({
            "id": m.get("id"),
            "name": m.get("name"),
            "type": m.get("type"),
            "kind": TYPES[model_type],
            "creator": (m.get("creator") or {}).get("username"),
            "nsfw": bool(m.get("nsfw")),
            "downloads": (m.get("stats") or {}).get("downloadCount"),
            "versions": versions,
        })
    return items


def resolve_file(version_id: int | str, name: str) -> tuple[str, int, str]:
    """(download_url, size, page_url) for one file of one version — re-fetched, never
    client-supplied.

    The browser sends an id and a filename; the URL is looked up here. A client that could
    hand the downloader an arbitrary URL would turn this app into a fetch-anything proxy,
    which is a much worse thing to have listening on your machine than a model browser.

    The page URL comes back too so the downloader can record where the file came from
    (`services/provenance.py`). It needs the *model* id, which only this response carries
    — a version id alone has no public page — so it is read here rather than reconstructed
    later from something the browser sent.
    """
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        raise CivitaiError(f"not a version id: {version_id!r}")
    data = _get(f"{API}/model-versions/{vid}")
    model_id = data.get("modelId")
    page = f"https://civitai.com/models/{model_id}?modelVersionId={vid}" if model_id else ""
    for f in data.get("files") or []:
        if f.get("name") == name:
            url = f.get("downloadUrl") or ""
            if not url:
                raise CivitaiError(f"{name} has no download url")
            return url, int((f.get("sizeKB") or 0) * 1024), page
    raise CivitaiError(f"version {vid} has no file named {name!r}")
