"""Downloading a model file into the directory a backend actually reads.

Multi-gigabyte transfers, so: a thread, byte progress over SSE, resume, and a cancel that
leaves the partial file behind to resume from.

**One at a time**, like stack actions — a second request is refused, not queued. Two 7 GB
transfers competing for the same link finish later than one after the other, and a queue
is state that would need its own persistence and cancellation semantics. Worth adding when
"pick five models and walk away" becomes a real want; it isn't yet.

Safety rules, all of which exist because the filename comes from a remote API:
- the destination is a **collection key** (kobold/checkpoint/lora/vae), never a path;
- the name is basenamed and validated by `inventory._safe_new_target`;
- bytes land in `<name>.part` and are renamed only on a verified-length completion, so a
  killed transfer can never look like a usable model to the backend that scans that dir.
"""
from __future__ import annotations

import itertools
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from services import civitai, inventory, provenance
from services.hub import UA, HubError

# A download only ever fetches from these hosts. The URL is built or looked up server-side
# (never sent by the browser), and this is the belt to that braces: if a resolver is ever
# changed to trust input, this still refuses to make the app a fetch-anything proxy.
ALLOWED_HOSTS = {"huggingface.co", "civitai.com"}

CHUNK = 1024 * 1024
RESOLVE = "https://huggingface.co/{repo}/resolve/main/{path}"


@dataclass
class Download:
    id: str
    source: str                     # "hf" | "civitai"
    url: str
    repo: str                       # display only: repo id, or the civitai model name
    path: str
    kind: str
    name: str
    dest: Path
    # The item's page on the site it came from, recorded when the transfer completes so
    # the inventory can link a file on disk back to where it came from. Resolved at start
    # (it needs an API call for Civitai) rather than at the end, where a failure would be
    # blamed on a download that actually succeeded.
    page: str = ""
    total: int = 0
    received: int = 0
    started: float = field(default_factory=time.time)
    state: str = "running"          # running | done | error | cancelled
    error: str | None = None
    activate: bool = False
    _cancel: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        elapsed = max(time.time() - self.started, 0.001)
        rate = self.received / elapsed
        remaining = max(self.total - self.received, 0)
        return {
            "id": self.id, "source": self.source, "repo": self.repo,
            "path": self.path, "kind": self.kind, "page": self.page,
            "name": self.name, "total": self.total, "received": self.received,
            "state": self.state, "error": self.error,
            "pct": round(self.received / self.total * 100, 1) if self.total else None,
            "rate": int(rate),
            "eta": int(remaining / rate) if rate > 1 and self.total else None,
            "label": f"{self.repo} · {self.name}",
        }


_lock = threading.Lock()
_current: Download | None = None
_last: Download | None = None
_counter = itertools.count(1)


class Busy(RuntimeError):
    pass


def current() -> Download | None:
    return _current


def get(job_id: str) -> Download | None:
    for d in (_current, _last):
        if d and d.id == job_id:
            return d
    return None


def cancel(job_id: str) -> bool:
    d = get(job_id)
    if not d or d.state != "running":
        return False
    d._cancel.set()
    return True


def _check_repo(repo: str) -> None:
    """Repo ids are `owner/name`. Validated here as well as in hub.files, because a
    request can reach this endpoint without ever having gone through a search — and a
    junk id otherwise burns the single download slot on a job that can only 404."""
    if not repo or repo.count("/") != 1 or any(c in repo for c in "?&# \t"):
        raise inventory.InventoryError(f"not a repo id: {repo!r}")


def _check_url(url: str) -> None:
    host = urllib.parse.urlparse(url).hostname or ""
    if host not in ALLOWED_HOSTS and not host.endswith(".civitai.com"):
        raise inventory.InventoryError(f"refusing to download from {host or url!r}")


def start_hf(repo: str, path: str, kind: str, activate: bool = False) -> Download:
    _check_repo(repo)
    # The remote path is reduced to its basename before it is ever joined to a directory,
    # so a crafted "../../x.gguf" names x.gguf in the collection rather than escaping it.
    # safe_new_target re-checks that on the resolved path.
    name = path.rsplit("/", 1)[-1]
    url = RESOLVE.format(repo=urllib.parse.quote(repo), path=urllib.parse.quote(path))
    page = "https://huggingface.co/" + "/".join(
        urllib.parse.quote(part) for part in repo.split("/"))
    return _start("hf", url, repo, path, kind, name, activate, page)


def start_civitai(version_id, name: str, kind: str, activate: bool = False) -> Download:
    """Resolve the URL from Civitai's own API — the browser sends an id, never a link."""
    try:
        url, _size, page = civitai.resolve_file(version_id, name)
    except civitai.CivitaiError as e:
        raise inventory.InventoryError(str(e))
    return _start("civitai", url, f"civitai:{version_id}", name, kind, name, activate, page)


def _start(source: str, url: str, repo: str, path: str, kind: str, name: str,
           activate: bool, page: str = "") -> Download:
    global _current
    _check_url(url)
    dest = inventory.safe_new_target(kind, name)          # raises InventoryError

    with _lock:
        if _current and _current.state == "running":
            raise Busy(f"already downloading {_current.name}")
        d = Download(id=f"{next(_counter)}-{int(time.time())}", source=source, url=url,
                     repo=repo, path=path, kind=kind, name=name, dest=dest,
                     activate=activate, page=page)
        _current = d

    threading.Thread(target=_run, args=(d,), name=f"dl-{d.id}", daemon=True).start()
    return d


def _run(d: Download) -> None:
    global _last
    part = d.dest.with_name(d.dest.name + ".part")
    try:
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(d.url, headers={"User-Agent": UA})
        token = os.environ.get(
            "CIVITAI_TOKEN" if d.source == "civitai" else "HF_TOKEN", "").strip()
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        if have:
            req.add_header("Range", f"bytes={have}-")

        with urllib.request.urlopen(req, timeout=30) as r:
            length = int(r.headers.get("Content-Length") or 0)
            # A server that ignored the Range header sends 200 and the whole file; keeping
            # the old bytes then would corrupt it, so start over rather than append.
            if have and r.status != 206:
                have = 0
                part.unlink(missing_ok=True)
            d.total = length + have
            d.received = have

            free = shutil.disk_usage(d.dest.parent).free
            if d.total and free < (d.total - have) * 1.05:
                raise OSError(f"not enough space: needs {d.total - have} bytes, {free} free")

            mode = "ab" if have else "wb"
            with open(part, mode) as f:
                while True:
                    if d._cancel.is_set():
                        d.state = "cancelled"
                        # Keep the .part: cancelling a 6 GB download 90% in and throwing
                        # the bytes away is a worse outcome than a resumable stub.
                        return
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    d.received += len(chunk)

        if d.total and d.received != d.total:
            raise OSError(f"short read: got {d.received} of {d.total} bytes")
        os.replace(part, d.dest)                          # atomic, same filesystem
        # Only now — the record must describe a file that actually exists, and a `.part`
        # that never completed would leave a link pointing at a model you don't have.
        # Before invalidate(), so the next listing to repopulate the cache already sees it.
        provenance.record(d.kind, d.name, d.source, d.page)
        inventory.invalidate()
        d.state = "done"

        if d.activate and d.kind == "kobold":
            # Selecting only writes model.env; it still takes a restart to load, and the
            # UI says so. Never restart a backend as a side effect of a download finishing.
            try:
                inventory.select_kobold(d.name, None)
            except inventory.InventoryError as e:
                d.error = f"downloaded, but could not select: {e}"

    except urllib.error.HTTPError as e:
        d.state = "error"
        # A bare "HTTP Error 401" sends you debugging the client. Say what fixes it — the
        # token var for this source, and whether one was sent at all — since a missing
        # token and a token without access to this file look identical otherwise.
        if e.code in (401, 403):
            var = "CIVITAI_TOKEN" if d.source == "civitai" else "HF_TOKEN"
            sent = bool(os.environ.get(var, "").strip())
            d.error = (f"HTTP {e.code}: the {var} that was sent has no access to this file"
                       if sent else
                       f"HTTP {e.code}: this file needs an account token — set {var} "
                       "(stack-ui/tokens.env) and restart the dashboard")
        else:
            d.error = str(e)
    except (urllib.error.URLError, HubError, OSError, ValueError) as e:
        d.state = "error"
        d.error = str(e)
    except Exception as e:                                # noqa: BLE001
        d.state = "error"
        d.error = f"{type(e).__name__}: {e}"
    finally:
        if d.state == "running":
            d.state = "error"
            d.error = d.error or "stopped unexpectedly"
        _last = d
