"""Invoking the `stack` script — the only thing here that touches the system.

Two rules this module exists to enforce:

1. **No shell.** Every call is a list argv to subprocess, never a string. Nothing a
   browser sends is ever interpolated into a command line.
2. **Allowlist, not validation.** Callers ask for a verb by key; unknown keys raise.
   Phase 0 exposes only the two read-only verbs, so this whole service cannot change
   the state of the machine.

`stack` is found the way `stack` finds everything else: AI_ECOSYSTEM if set, else the
parent of this project, with the checkout expected as a sibling.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ECO = Path(os.environ.get("AI_ECOSYSTEM") or BASE.parent)
STACK = ECO / "ai-stack" / "stack"

# Phase 0 is read-only by construction. Mutating verbs (on/off/up/down/restart) arrive
# in phase 1 — adding them here is the *only* place that needs to change, which is why
# they aren't merely disabled in the UI.
VERBS: dict[str, list[str]] = {
    "status": ["status"],
    "doctor": ["doctor"],
}

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


class StackError(RuntimeError):
    pass


def available() -> bool:
    return STACK.is_file() and os.access(STACK, os.X_OK)


def run(verb: str, timeout: int = 30) -> dict:
    """Run an allowlisted verb. Returns exit code + de-coloured output."""
    if verb not in VERBS:
        raise StackError(f"verb not allowed: {verb!r}")
    if not available():
        raise StackError(f"stack not found or not executable at {STACK}")
    started = time.monotonic()
    try:
        p = subprocess.run(
            [str(STACK), *VERBS[verb]],
            capture_output=True, text=True, timeout=timeout,
            # A clean env with just enough to work: `stack` shells out to podman and
            # systemctl, which need HOME/PATH/XDG_RUNTIME_DIR to find the user session.
            env={
                "HOME": os.environ.get("HOME", ""),
                "PATH": os.environ.get("PATH", ""),
                "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", ""),
                "USER": os.environ.get("USER", ""),
                **({"AI_ECOSYSTEM": os.environ["AI_ECOSYSTEM"]} if "AI_ECOSYSTEM" in os.environ else {}),
            },
        )
    except subprocess.TimeoutExpired:
        raise StackError(f"`stack {verb}` timed out after {timeout}s")
    return {
        "verb": verb,
        "exit_code": p.returncode,
        "output": strip_ansi(p.stdout + p.stderr).rstrip(),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


# --- parsing -----------------------------------------------------------------
# `stack status` prints for humans, so this is text-scraping and it is the one brittle
# seam in the app. Everything degrades to showing the raw text if a line doesn't match,
# and `raw` is always returned so the UI can fall back wholesale. The real fix is a
# `stack status --json` mode — worth doing when phase 1 needs structured data anyway.

_ROW = re.compile(r"^\s+(?P<name>\S+)\s+\[(?P<state>up|down)\]\s+:(?P<port>\d+)\s+(?P<desc>.*?)\s*$")
_SWITCH = re.compile(r"^AI backend switch:\s*(?P<state>ON|OFF)\b")
_GROUP = re.compile(r"^(?P<group>Backends|Frontends|Control)\b")


def parse_status(output: str) -> dict:
    switch, group, apps = None, None, []
    for line in output.splitlines():
        if m := _SWITCH.match(line):
            switch = m.group("state")
            continue
        if m := _GROUP.match(line):
            group = m.group("group").lower()
            continue
        if m := _ROW.match(line):
            apps.append({
                "name": m.group("name"),
                "up": m.group("state") == "up",
                "port": int(m.group("port")),
                "desc": m.group("desc"),
                "group": group or "other",
            })
    return {"switch": switch, "apps": apps, "parsed": bool(apps)}


_DOCTOR_OK = re.compile(r"^\s+✓\s+(.*)$")
_DOCTOR_WARN = re.compile(r"^\s+!\s+(.*)$")


def parse_doctor(output: str) -> dict:
    """Split doctor's ✓/! lines out so the UI can show a count without re-running it."""
    ok = [m.group(1) for line in output.splitlines() if (m := _DOCTOR_OK.match(line))]
    warn = [m.group(1) for line in output.splitlines() if (m := _DOCTOR_WARN.match(line))]
    return {"ok": ok, "warnings": warn, "healthy": not warn}


def disk() -> dict:
    """Free space where the models live — the number that decides what you can download."""
    total, used, free = shutil.disk_usage(ECO)
    return {"path": str(ECO), "total": total, "used": used, "free": free}
