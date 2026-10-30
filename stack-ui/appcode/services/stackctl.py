"""Invoking the `stack` script — the only thing here that touches the system.

Three rules this module exists to enforce:

1. **No shell.** Every call is a list argv to subprocess, never a string. Nothing a
   browser sends is ever interpolated into a command line.
2. **Allowlist, not validation.** Callers ask for a verb by key; the key maps to a fixed
   argv. A verb that isn't in VERBS cannot be run — which is why phase 0 was read-only
   *by construction*, and why this table is where review attention belongs now that
   mutating verbs live in it.
3. **Targets come from the registry, not the request.** An app name is only accepted if
   `stack status` currently lists it. The browser can't name a process to act on.

`stack` is found the way `stack` finds everything else: AI_ECOSYSTEM if set, else the
parent of this project, with the checkout expected as a sibling.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# stack-ui/appcode/services/stackctl.py -> parents[2] is stack-ui, parents[4] the ecosystem.
BASE = Path(__file__).resolve().parents[2]
ECO = Path(os.environ.get("AI_ECOSYSTEM") or BASE.parent.parent)
STACK = ECO / "ai-stack" / "stack"

# This app's own name in the registry. It is never a legal target: `down stackui` or
# `restart stackui` issued through the UI would kill the server mid-request, leaving the
# page hanging on a reply that can never come. Stop it from a terminal.
SELF_APP = "stackui"

# `down` accepts group names as well as apps.
DOWN_GROUPS = {"all", "backends", "frontends"}


@dataclass(frozen=True)
class Verb:
    argv: list[str]                 # fixed prefix; the validated target is appended
    mutating: bool
    target: str | None = None       # None | "app" | "down"
    stream: bool = False            # long-running: stream output instead of buffering
    timeout: int = 30


VERBS: dict[str, Verb] = {
    # read-only
    "status":  Verb(["status"], False),
    "doctor":  Verb(["doctor"], False, timeout=60),
    "logs":    Verb(["logs"], False, target="app", stream=True),
    # mutating — phase 1
    "on":      Verb(["on"], True, stream=True),
    "off":     Verb(["off"], True, stream=True),
    "up":      Verb(["up"], True, stream=True),
    "down":    Verb(["down"], True, target="down", stream=True),
    "restart": Verb(["restart"], True, target="app", stream=True),
    # `stack <app>` — the bare-app-name form that starts one thing
    "start":   Verb([], True, target="app", stream=True),
    # Re-pins the ComfyUI custom nodes (clone/fetch/checkout, idempotent). The target is
    # baked into the argv rather than accepted from the request: `install` also has
    # `service` and `path` targets, and those write under $HOME — which, in this
    # container, is a throwaway directory. Exposing the whole verb would offer two
    # buttons that quietly do nothing useful.
    "install_comfyui": Verb(["install", "comfyui"], True, stream=True, timeout=600),
    # Pantheon's desktop avatar. It has no port and no health URL, so it is not an
    # app target -- hence a fixed argv here rather than target="app".
    #
    # This container has the host session bus but no Wayland display, so it cannot
    # put a window on screen itself. `stack desktop` does not try: it asks the host
    # user manager to start pantheon-desktop.service, which inherits the display
    # from the session. The systemctl shim carries that call out of here.
    "desktop":      Verb(["desktop"], True, stream=True, timeout=90),
    "desktop_stop": Verb(["desktop", "stop"], True, stream=True, timeout=60),
}

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


class StackError(RuntimeError):
    pass


def available() -> bool:
    return STACK.is_file() and os.access(STACK, os.X_OK)


DESKTOP_UNIT = "pantheon-desktop.service"


def desktop_active() -> bool:
    """Is the desktop avatar running?

    Asked of the host user manager through the systemctl shim, because the unit
    lives there and not in this container. Any failure -- no shim, no unit, no
    bus -- reads as "not running", which is the safe answer: the button then
    offers to start it, and starting something already up is a no-op.
    """
    exe = shutil.which("systemctl")
    if not exe:
        return False
    try:
        p = subprocess.run([exe, "--user", "is-active", DESKTOP_UNIT],
                           capture_output=True, text=True, timeout=5)
        return p.stdout.strip() == "active"
    except Exception:                                   # noqa: BLE001
        return False


# Passed through to `stack` when present. Everything else in the environment is dropped:
# a small explicit list is easier to reason about than "inherit and hope".
#   CONTAINER_HOST            — the host podman engine. Without it, podman in the
#                               container talks to an engine that isn't there, so every
#                               up/down/restart silently does nothing.
#   DBUS_SESSION_BUS_ADDRESS  — the host user systemd, via the systemctl shim. Without it
#                               `stack` can't see the unit and the on/off switch vanishes
#                               from status entirely (it reads as "unknown", not "off").
# STACK_GPU: the host's GPU, set by run-podman.sh. The start scripts can't detect it from
# in here, and without it they launch the backends with no GPU (lib/host.sh).
_PASSTHROUGH = ("AI_ECOSYSTEM", "CONTAINER_HOST", "DBUS_SESSION_BUS_ADDRESS", "STACK_GPU")


def _env() -> dict:
    """Minimal environment: enough for podman and systemctl to find the user session."""
    e = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", ""),
        "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", ""),
        "USER": os.environ.get("USER", ""),
    }
    for k in _PASSTHROUGH:
        if k in os.environ:
            e[k] = os.environ[k]
    return e


# --- the registry ------------------------------------------------------------
_APPS: tuple[float, list[str]] = (0.0, [])
_APPS_TTL = 60.0


def invalidate_apps() -> None:
    """Drop the cached registry — call after anything that could change what's running."""
    global _APPS
    _APPS = (0.0, [])


def known_apps(force: bool = False) -> list[str]:
    """App names `stack status` currently lists. The allowlist for every target."""
    global _APPS
    if not force and time.monotonic() - _APPS[0] < _APPS_TTL and _APPS[1]:
        return _APPS[1]
    names = [a["name"] for a in parse_status(run("status")["output"])["apps"]]
    if names:
        _APPS = (time.monotonic(), names)
    return names


def resolve(verb_key: str, target: str | None) -> list[str]:
    """Validate a (verb, target) pair into a concrete argv. Raises on anything unknown."""
    v = VERBS.get(verb_key)
    if v is None:
        raise StackError(f"verb not allowed: {verb_key!r}")
    if v.target is None:
        if target:
            raise StackError(f"`{verb_key}` takes no target")
        return [*v.argv]

    if not target:
        raise StackError(f"`{verb_key}` needs a target")
    apps = known_apps()
    if not apps:
        # Better to refuse than to fall back to trusting the request: an empty registry
        # means we can't tell a real app from anything else the browser sent.
        raise StackError("registry unavailable — cannot validate the target")

    if v.target == "down":
        if target not in DOWN_GROUPS and target not in apps:
            raise StackError(f"unknown target: {target!r}")
    elif target not in apps:
        raise StackError(f"unknown app: {target!r}")

    if v.mutating and target == SELF_APP:
        raise StackError(f"refusing to {verb_key} {SELF_APP} from its own UI — "
                         f"use a terminal: stack down {SELF_APP}")
    return [*v.argv, target]


# --- running -----------------------------------------------------------------
def run(verb_key: str, target: str | None = None) -> dict:
    """Run to completion and buffer the output. For the quick read-only verbs."""
    v = VERBS[verb_key] if verb_key in VERBS else None
    argv = resolve(verb_key, target)
    if not available():
        raise StackError(f"stack not found or not executable at {STACK}")
    started = time.monotonic()
    try:
        p = subprocess.run([str(STACK), *argv], capture_output=True, text=True,
                           timeout=(v.timeout if v else 30), env=_env())
    except subprocess.TimeoutExpired:
        raise StackError(f"`stack {' '.join(argv)}` timed out")
    return {
        "verb": verb_key, "target": target, "exit_code": p.returncode,
        "output": strip_ansi(p.stdout + p.stderr).rstrip(),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def stream(verb_key: str, target: str | None = None) -> Iterator[tuple[str, object]]:
    """Yield ("line", str) as output arrives, then ("exit", int).

    Used for the slow verbs — `stack up` waits on health checks and can take a couple of
    minutes on a cold model load, which is far too long to hold a request open blind.
    The caller MUST close this generator (or exhaust it); closing terminates the child.
    """
    argv = resolve(verb_key, target)
    if not available():
        raise StackError(f"stack not found or not executable at {STACK}")
    p = subprocess.Popen(
        [str(STACK), *argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, env=_env(),
        # Own process group: `stack logs` execs `podman logs -f`, and killing only the
        # parent would leave that child attached to the pipe forever.
        start_new_session=True,
    )
    try:
        for line in p.stdout:                      # type: ignore[union-attr]
            yield "line", strip_ansi(line.rstrip("\n"))
        p.wait()
        yield "exit", p.returncode
    finally:
        if p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), 15)
            except (ProcessLookupError, PermissionError):
                p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        if p.stdout:
            p.stdout.close()


# --- parsing -----------------------------------------------------------------
# `stack status` prints for humans, so this is text-scraping and it is the one brittle
# seam in the app. Everything degrades to showing the raw text if a line doesn't match,
# and `raw` is always returned so the UI can fall back wholesale. The real fix is a
# `stack status --json` mode.

_ROW = re.compile(r"^\s+(?P<name>\S+)\s+\[(?P<state>up|down)\s*\]\s+:(?P<port>\d+)\s+(?P<desc>.*?)\s*$")

# Facing names come out of the `stack` script's LABEL array, not a copy kept here — the
# same reason nodes.py reads CUSTOM_NODES from it. Display only: `name` stays the registry
# key, so every target check and every button still acts on what the CLI accepts.
_LABEL_BLOCK = re.compile(r"^declare -A LABEL=\((.*?)^\)", re.S | re.M)
_LABEL_ROW = re.compile(r'^\s*\[([A-Za-z0-9_.-]+)\]="([^"]*)"', re.M)


def labels() -> dict:
    """{registry key: product name} for the apps whose name differs from their key."""
    try:
        block = _LABEL_BLOCK.search(STACK.read_text())
    except OSError:
        return {}
    return dict(_LABEL_ROW.findall(block.group(1))) if block else {}
_SWITCH = re.compile(r"^AI backend switch:\s*(?P<state>ON|OFF)\b")
_GROUP = re.compile(r"^(?P<group>Backends|Frontends|Control)\b")


def parse_status(output: str) -> dict:
    switch, group, apps = None, None, []
    label = labels()
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
                "label": label.get(m.group("name"), m.group("name")),
                "up": m.group("state").strip() == "up",
                "port": int(m.group("port")),
                "desc": m.group("desc"),
                "group": group or "other",
                # The UI greys out controls it must not offer rather than failing later.
                "self": m.group("name") == SELF_APP,
            })
    return {"switch": switch, "apps": apps, "parsed": bool(apps)}


_DOCTOR_OK = re.compile(r"^\s+✓\s+(.*)$")
_DOCTOR_WARN = re.compile(r"^\s+!\s+(.*)$")


def parse_doctor(output: str) -> dict:
    ok = [m.group(1) for line in output.splitlines() if (m := _DOCTOR_OK.match(line))]
    warn = [m.group(1) for line in output.splitlines() if (m := _DOCTOR_WARN.match(line))]
    return {"ok": ok, "warnings": warn, "healthy": not warn}


def disk() -> dict:
    """Free space where the models live — the number that decides what you can download."""
    total, used, free = shutil.disk_usage(ECO)
    return {"path": str(ECO), "total": total, "used": used, "free": free}
