"""The Credentials screen's server side: what each saved token covers, and testing one.

A plain module (not a BFF), so ``StackApi`` stays stateless. The tokens themselves live in the
store ``services.credentials`` manages (``stackctl.CREDENTIALS``); nothing here ever returns
one to the browser.

- **Covers:** each installed frontend's https origin is matched against the saved prefixes the
  way git's helper does (longest prefix wins), so a card lists exactly the frontends that would
  use its token. An origin with a user name or token written into it is reported without them.
- **Test** a token by doing what the stack does with it: ``git ls-remote`` on every installed
  frontend it covers, with **only** the stack's helper configured (no global or system git
  config, so your own login can't answer instead and make a dead token look fine). For
  github.com it also asks GitHub's ``/rate_limit``, which every token may read and which counts
  against no limit: a 401 means GitHub no longer accepts the token (expired or revoked), and
  GitHub names its expiry date in a response header. The result is saved on the entry
  (``credentials.annotate``) so the card still shows it next time.
"""
from __future__ import annotations

import datetime
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from services import credentials

GITHUB_PROBE = "https://api.github.com/rate_limit"
TIMEOUT = 15
_USERINFO = re.compile(r"^(https://)[^/@]+@", re.I)


def clean_url(url: str) -> str:
    """An origin URL without any user name or token written into it."""
    return _USERINFO.sub(r"\1", url.strip())


def _store():
    from services.stackctl import CREDENTIALS

    return CREDENTIALS


def installed_origins() -> list[dict]:
    """Installed frontends with their origin URL: [{repo, brand, origin}]. Best effort: a
    frontend whose origin git can't read is left out."""
    from services import stackctl

    out = []
    try:
        fronts = stackctl.frontends(force=True).get("frontends") or []
    except stackctl.StackError:
        return out
    for f in fronts:
        if not isinstance(f, dict) or not f.get("dir"):
            continue
        try:
            p = subprocess.run(["git", "-C", str(f["dir"]), "remote", "get-url", "origin"],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if p.returncode == 0 and p.stdout.strip():
            out.append({"repo": str(f.get("repo") or ""), "brand": str(f.get("brand") or ""),
                        "origin": clean_url(p.stdout)})
    return out


def overview() -> dict:
    """{credentials: [listing row + covers: [repo]], frontends: [{repo, brand, origin,
    prefix (the saved one it uses, or ""), suggested (where a token for it would go, or "")}]}"""
    store = _store()
    rows = credentials.listing(store)
    prefixes = [r["prefix"] for r in rows]
    fronts = installed_origins()
    for f in fronts:
        f["prefix"] = credentials.best_prefix(f["origin"], prefixes) or ""
        f["suggested"] = credentials.prefix_for(f["origin"]) or ""
    for r in rows:
        r["covers"] = [f["repo"] for f in fronts if f["prefix"] == r["prefix"]]
    return {"credentials": rows, "frontends": fronts}


def _ls_remote(url: str, store) -> tuple[bool, str]:
    """``git ls-remote`` with only the stack's helper: does the saved token reach this repo?"""
    env = {
        "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp"),
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": '!"$STACK_PY" "$STACK_CREDPY" --store "$STACK_CREDS"',
        "STACK_PY": sys.executable, "STACK_CREDPY": credentials.__file__,
        "STACK_CREDS": str(store), "STACK_GIT_URL": url,
    }
    try:
        p = subprocess.run(["git", "ls-remote", "--", url, "HEAD"], capture_output=True,
                           text=True, timeout=TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return False, "no answer in 15 s"
    except OSError as exc:
        return False, f"git didn't run: {exc.strerror or exc}"
    if p.returncode == 0:
        return True, "can read it"
    err = (p.stderr or "").lower()
    if any(w in err for w in ("authentication", "403", "401", "not found", "could not read")):
        return False, "no access with this token"
    return False, "couldn't reach it"


def _github(token: str) -> dict:
    """{ok, note, expires}: does GitHub still accept this token, and until when?"""
    req = urllib.request.Request(GITHUB_PROBE, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "AI-Ecosystem",
        "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 - fixed https URL
            expires = str(r.headers.get("github-authentication-token-expiration") or "")[:10]
            return {"ok": True, "note": "GitHub accepts it", "expires": expires}
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return {"ok": False, "note": "GitHub refuses it: expired or revoked", "expires": ""}
        return {"ok": None, "note": f"GitHub answered {exc.code}", "expires": ""}
    except Exception:  # noqa: BLE001 - offline is a normal state
        return {"ok": None, "note": "couldn't reach GitHub", "expires": ""}


def test(prefix: str) -> dict:
    """Try one saved token. {prefix, passed: True|False|None (couldn't tell), note, expires,
    repos: [{repo, ok, note}]}; the outcome is also saved on the entry. (``passed``, not
    ``ok``: the BFF's own ``ok`` says whether the call worked.)"""
    store = _store()
    creds = credentials.load(store)
    if prefix not in creds:
        raise KeyError(prefix)
    host = prefix.split("/", 1)[0]
    fronts = [f for f in installed_origins()
              if credentials.best_prefix(f["origin"], creds) == prefix]
    with ThreadPoolExecutor(max_workers=4) as pool:
        repos = [{"repo": f["repo"], **dict(zip(("ok", "note"), res))}
                 for f, res in zip(fronts, pool.map(lambda f: _ls_remote(f["origin"], store), fronts))]
    gh = _github(creds[prefix]["token"]) if host == "github.com" else None
    results = [r["ok"] for r in repos] + ([gh["ok"]] if gh and gh["ok"] is not None else [])
    if gh and gh["ok"] is False:
        ok, note = False, gh["note"]
    elif repos and not all(r["ok"] for r in repos):
        ok, note = False, "; ".join(f'{r["repo"]}: {r["note"]}' for r in repos if not r["ok"])
    elif results:
        ok = True
        note = (f"reads {', '.join(r['repo'] for r in repos)}" if repos else gh["note"])
    else:
        ok, note = None, ("no installed frontend uses it yet, so there was nothing to try"
                          if not gh else gh["note"])
    expires = gh["expires"] if gh else ""
    credentials.annotate(store, prefix, checked=datetime.date.today().isoformat(),
                         check_ok=ok, check_note=note, expires=expires)
    return {"prefix": prefix, "passed": ok, "note": note, "expires": expires, "repos": repos}
