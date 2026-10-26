"""Where a model on disk came from.

The inventory is a directory listing: a file has a name and a size, and nothing about a
`.gguf` sitting in `models/` says which repo it was pulled from. A search result *is* a
remote item, so linking one out is free (phase 3/4); an inventory row can only link out if
the origin was written down at the moment we fetched it. So the downloader records it here
and the inventory joins on the filename.

**Recorded, never guessed.** Models that predate this store — or that were copied in by
hand, which is most of the 85 GB — have no entry, and the UI falls back to a *search* link
that says it is a search. A link that claims to be the model's page and lands on a list of
near-matches is worse than no link at all.

The store lives at the ecosystem root because it spans both backends' model directories,
and it is a cache of provenance rather than a source of truth about what exists: a missing,
unreadable or corrupt file degrades to "no entry" and must never block a listing.
"""
from __future__ import annotations

import json
import os
import threading

from services.stackctl import ECO

STORE = ECO / "model-sources.json"

# Writes come from the download thread, deletes from a request thread; one lock covers
# the read-modify-write, and the rename itself is atomic for anyone reading concurrently.
_lock = threading.Lock()


def _key(kind: str, name: str) -> str:
    return f"{kind}/{name}"


def load() -> dict[str, dict]:
    try:
        data = json.loads(STORE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("url")}


def _save(data: dict[str, dict]) -> None:
    tmp = STORE.with_name(STORE.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, STORE)              # atomic within the same directory


def record(kind: str, name: str, source: str, url: str) -> None:
    """Note that (kind, name) came from `url`. Best-effort: a failure here must not turn a
    finished 7 GB download into an error."""
    if not url:
        return
    try:
        with _lock:
            data = load()
            data[_key(kind, name)] = {"source": source, "url": url}
            _save(data)
    except OSError:
        pass


def forget(kind: str, name: str) -> None:
    """Drop the entry for a deleted model, so a later file of the same name can't inherit
    a link to something it isn't."""
    try:
        with _lock:
            data = load()
            if data.pop(_key(kind, name), None) is not None:
                _save(data)
    except OSError:
        pass


def urls(kind: str) -> dict[str, str]:
    """`{filename: url}` for one collection — one read per listing, not one per file."""
    prefix = f"{kind}/"
    return {k[len(prefix):]: v["url"] for k, v in load().items() if k.startswith(prefix)}
