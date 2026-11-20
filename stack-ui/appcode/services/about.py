"""
What AI Ecosystem is and where it lives: the project's links and version comparison.
**Both sides**: the About screen shows the links in the browser, and the server's release
check (``release_check``) compares versions with ``is_newer``. Pure data and functions, so
the browser can import it (import *names* from it, never the module: the packager only
follows ``from services.about import X``).

The version itself is not here. It lives in ``ai-stack/VERSION`` and reaches the page
through ``stack status --json`` (``release``), so the dashboard can never disagree with
``stack version``. Same shape as IguanaXterm's ``about.py``, minus its own ``VERSION``.
"""
from __future__ import annotations

import re

__all__ = ["NAME", "REPO", "REPO_URL", "WIKI_URL", "RELEASES_URL", "ISSUES_URL",
           "CHANGELOG_URL", "parse_version", "is_newer"]

NAME = "AI Ecosystem"
REPO = "JotunheimPress/ai_ecosystem"
REPO_URL = f"https://github.com/{REPO}"
WIKI_URL = f"{REPO_URL}/wiki"
RELEASES_URL = f"{REPO_URL}/releases"
ISSUES_URL = f"{REPO_URL}/issues"
CHANGELOG_URL = f"{REPO_URL}/blob/master/CHANGELOG.md"

_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")


def parse_version(text) -> tuple[int, int, int] | None:
    """``"v1.2.0"`` → ``(1, 2, 0)``; ``None`` for anything else, pre-releases included."""
    match = _VERSION.match(str(text or "").strip())
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def is_newer(latest, current) -> bool:
    """Whether ``latest`` is a later release than ``current``. False if either is unreadable."""
    a, b = parse_version(latest), parse_version(current)
    return bool(a and b and a > b)
