"""ComfyUI custom nodes: what `stack` pins them to, and what's actually checked out.

`stack doctor` already reports the three states this cares about. This reads the same
source of truth — the `CUSTOM_NODES=(...)` registry inside the `stack` script — rather
than keeping a second copy of the pins, because a dashboard that disagreed with `stack`
about which commit is correct would be worse than no dashboard.

Nothing here writes. Fixing drift is `stack install comfyui`, which is idempotent and
already knows how to clone, fetch and re-checkout; re-implementing that in Python would be
the same duplication the systemctl shim exists to avoid.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .inventory import COMFY_MODELS
from .stackctl import ECO, STACK, _env

NODES_DIR = COMFY_MODELS.parent / "custom_nodes"

# The registry is a bash array of "name|repo|pinned-sha" rows. Parsed rather than
# duplicated — see the module docstring.
_BLOCK = re.compile(r"^CUSTOM_NODES=\((.*?)^\)", re.S | re.M)
_ROW = re.compile(r'"([^"|]+)\|([^"|]+)\|([0-9a-f]{7,40})"')


def registry() -> list[dict]:
    try:
        text = STACK.read_text()
    except OSError:
        return []
    block = _BLOCK.search(text)
    if not block:
        return []
    return [{"name": n, "repo": r, "pin": p} for n, r, p in _ROW.findall(block.group(1))]


def _head(path: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10, env=_env())
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def status() -> dict:
    """Each pinned node's state. Mirrors `stack doctor`'s three cases exactly."""
    out = []
    for row in registry():
        d = NODES_DIR / row["name"]
        entry = {**row, "dir": str(d), "head": None, "state": "missing"}
        if (d / ".git").is_dir():
            head = _head(d)
            entry["head"] = head
            if head is None:
                entry["state"] = "unreadable"
            elif head == row["pin"]:
                entry["state"] = "on_pin"
            else:
                entry["state"] = "off_pin"
        elif d.exists():
            # `stack install` reports and leaves these alone rather than clobbering, so
            # say the same thing here instead of offering a fix that won't touch it.
            entry["state"] = "not_git"
        out.append(entry)
    return {
        "dir": str(NODES_DIR),
        "nodes": out,
        "drift": any(n["state"] != "on_pin" for n in out),
        # Deps are baked into the ComfyUI image, so a node that arrives via a re-pin may
        # still need a rebuild. Flagged because the failure — a node that imports and then
        # errors at runtime — doesn't look like a missing dependency.
        "note": "python deps for these live in comfyui/Containerfile, not here",
    }
