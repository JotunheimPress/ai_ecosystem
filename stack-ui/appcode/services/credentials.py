"""Tokens for private git repos, matched by URL prefix: the stack's credential store.

``stack-ui/credentials.json`` (git-ignored, mode 600) maps a prefix to a token::

    {"version": 1, "credentials": {
        "github.com/El-Iguana":     {"token": "github_pat_…", "added": "2026-11-27"},
        "github.com/JotunheimPress": {"token": "github_pat_…"},
        "gitlab.com":               {"token": "glpat-…", "username": "oauth2"}}}

A prefix is a host, optionally followed by path segments (owner, repo). The longest one that
matches a repo's URL wins, so a fine-grained GitHub token, which covers one owner, sits under
that owner and two owners each get their own. Matching ignores case and a trailing ``.git``.

**Two users, one file.** ``stack`` runs this as a git credential helper (``get``, which is
what git calls; ``store`` and ``erase`` do nothing): it is appended after the user's own
helpers, so their login answers first. The dashboard imports it to save a token pasted after
a failed install, and ``release_check`` to look one up. It is read every time, so a new token
works without restarting anything.

Standalone on purpose (stdlib only, no imports from ``services``): ``stack`` runs this file
by its path in the checkout, on the host and in the dashboard's container alike.

CLI (``--store PATH`` first)::

    get | store | erase        git's credential-helper protocol on stdin
    set URL|PREFIX [USERNAME]  save the token read from stdin; prints the prefix
    remove PREFIX              forget one
    list [--json]              what is saved, tokens hidden
    prefix URL                 the prefix a token for URL would be saved under
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import tempfile
from pathlib import Path

VERSION = 1
# host[:port] then path segments; what a prefix may be (and what a URL reduces to).
PREFIX = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d{1,5})?(/[A-Za-z0-9._~-]+)*$")
# A token is one printable word: no spaces, no newlines (it goes into git's protocol).
TOKEN = re.compile(r"^[\x21-\x7e]{8,1024}$")
_HTTPS = re.compile(r"^https://(?:[^/@]+@)?([^/]+)(/.*)?$", re.I)


class CredentialError(ValueError):
    pass


def key_of(url: str) -> str | None:
    """``https://github.com/Owner/repo.git`` → ``github.com/owner/repo``. None for anything
    that isn't https (ssh uses keys; a token can't help it)."""
    m = _HTTPS.match(url.strip())
    if not m:
        return None
    host = m.group(1).lower().rstrip(".")
    if host.endswith(":443"):
        host = host[:-4]
    path = (m.group(2) or "").split("?", 1)[0].split("#", 1)[0].strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    key = "/".join([host, *[p for p in path.split("/") if p]]).lower()
    return key if PREFIX.match(key) else None


def prefix_for(url: str) -> str | None:
    """Where a token for this repo is saved by default: its host and owner
    (``github.com/owner``), which is what a fine-grained GitHub token covers; just the host
    for a repo with no owner in its path."""
    key = key_of(url)
    if key is None:
        return None
    parts = key.split("/")
    return "/".join(parts[:2]) if len(parts) >= 3 else parts[0]


def _clean_prefix(prefix: str) -> str:
    p = prefix.strip().lower().rstrip("/")
    if p.startswith("https://"):
        p = key_of(p) or ""
    if p.endswith(".git"):
        p = p[:-4]
    if not PREFIX.match(p):
        raise CredentialError(f"not a host or host/owner prefix: {prefix!r}")
    return p


def load(store: Path) -> dict:
    """{prefix: entry}. A missing or unreadable file is an empty store, never an error."""
    try:
        data = json.loads(Path(store).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    creds = data.get("credentials") if isinstance(data, dict) else None
    if not isinstance(creds, dict):
        return {}
    return {k.lower(): v for k, v in creds.items()
            if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("token"), str)}


def _write(store: Path, creds: dict) -> None:
    """Atomically, mode 600 from the first byte (never world-readable, even briefly)."""
    store = Path(store)
    store.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".credentials-", dir=store.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": VERSION, "credentials": dict(sorted(creds.items()))}, f, indent=2)
            f.write("\n")
        os.replace(tmp, store)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def default_username(host: str) -> str:
    """What to send as the user name with a token: GitLab wants ``oauth2``; GitHub takes any,
    and ``x-access-token`` is what its own docs use (Gitea and others take any too)."""
    return "oauth2" if host.split(":")[0].startswith("gitlab.") else "x-access-token"


def lookup(url: str, store: Path) -> tuple[str, str] | None:
    """(username, token) of the longest saved prefix matching ``url``, or None."""
    key = key_of(url)
    if key is None:
        return None
    best = None
    for prefix, entry in load(store).items():
        if (key == prefix or key.startswith(prefix + "/")) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, entry)
    if best is None:
        return None
    host = best[0].split("/", 1)[0]
    return str(best[1].get("username") or default_username(host)), best[1]["token"]


def save(store: Path, where: str, token: str, username: str = "") -> str:
    """Save ``token`` under ``where`` (a URL: its host/owner; or a prefix). Returns the prefix."""
    token = token.strip()
    if not TOKEN.match(token):
        raise CredentialError("that doesn't look like a token (one word, no spaces)")
    prefix = (prefix_for(where) if "://" in where else None) or _clean_prefix(where)
    entry = {"token": token, "added": datetime.date.today().isoformat()}
    if username.strip():
        entry["username"] = username.strip()
    creds = load(store)
    creds[prefix] = entry
    _write(store, creds)
    return prefix


def remove(store: Path, prefix: str) -> bool:
    creds = load(store)
    p = _clean_prefix(prefix)
    if p not in creds:
        return False
    del creds[p]
    _write(store, creds)
    return True


def listing(store: Path) -> list[dict]:
    """What is saved, with the token reduced to its last four characters."""
    return [{"prefix": p, "added": str(e.get("added") or ""),
             "username": str(e.get("username") or ""), "hint": "…" + e["token"][-4:]}
            for p, e in sorted(load(store).items())]


# --- git's credential-helper protocol -------------------------------------------------
def helper_get(fields: dict, store: Path, env: dict) -> str:
    """Answer git's ``get``. The repo comes from git's ``path`` when it sends one
    (``credential.useHttpPath``), else from ``STACK_GIT_URL`` (the repo ``stack`` is about to
    clone or fetch) when it names the same host, else just the host. ``STACK_GIT_TOKEN`` (the
    old single ``GITHUB_TOKEN``) answers for github.com when nothing saved does."""
    if fields.get("protocol") != "https" or not fields.get("host"):
        return ""
    host = fields["host"].lower()
    url = f"https://{host}/{fields.get('path') or ''}"
    hinted = key_of(env.get("STACK_GIT_URL", ""))
    if not fields.get("path") and hinted and hinted.split("/", 1)[0] == host:
        url = env["STACK_GIT_URL"]
    found = lookup(url, store)
    if found is None and host == "github.com" and env.get("STACK_GIT_TOKEN", "").strip():
        found = ("x-access-token", env["STACK_GIT_TOKEN"].strip())
    if found is None:
        return ""
    return f"username={found[0]}\npassword={found[1]}\n"


def _read_fields(stream) -> dict:
    fields = {}
    for line in stream:
        line = line.rstrip("\n")
        if not line:
            break
        k, _, v = line.partition("=")
        fields[k] = v
    return fields


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[0] != "--store":
        print(__doc__.split("CLI", 1)[1], file=sys.stderr)
        return 2
    store, cmd, args = Path(argv[1]), argv[2], argv[3:]
    try:
        if cmd == "get":
            sys.stdout.write(helper_get(_read_fields(sys.stdin), store, dict(os.environ)))
        elif cmd in ("store", "erase"):
            _read_fields(sys.stdin)     # git tells every helper; this store is edited only by hand
        elif cmd == "set" and args:
            print(save(store, args[0], sys.stdin.readline(), args[1] if len(args) > 1 else ""))
        elif cmd == "remove" and len(args) == 1:
            if not remove(store, args[0]):
                print(f"nothing saved for {args[0]}", file=sys.stderr)
                return 1
        elif cmd == "list":
            rows = listing(store)
            if args[:1] == ["--json"]:
                print(json.dumps({"credentials": rows}))
            else:
                for r in rows:
                    print(f"  {r['prefix']:<40} {r['hint']}  {r['added']}")
                if not rows:
                    print("  no tokens saved")
        elif cmd == "prefix" and len(args) == 1:
            p = prefix_for(args[0])
            if p is None:
                return 1
            print(p)
        else:
            print(f"unknown or incomplete command: {cmd} {' '.join(args)}".strip(), file=sys.stderr)
            return 2
    except CredentialError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
