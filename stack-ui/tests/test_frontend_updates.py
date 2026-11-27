"""services.frontend_updates: the cached `stack frontend check --json` behind the Frontends
cards' "vX.Y.Z available" badge. `stackctl.run` is faked; the command itself is tested in
test_frontend_install.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "appcode"))

from services import frontend_updates as fu, stackctl  # noqa: E402

ROW = {"repo": "demo", "current": "v1.0.0", "latest": "v1.1.0", "newer": True,
       "checked": True, "reason": "", "origin": "https://github.com/acme/demo.git"}


@pytest.fixture()
def stack(monkeypatch):
    monkeypatch.setattr(fu, "_cache", {})
    monkeypatch.delenv("STACK_UPDATE_CHECK", raising=False)
    calls = []

    def answer(out):
        def run(verb, target=None):
            calls.append(verb)
            if isinstance(out, Exception):
                raise out
            return {"stdout": out}
        monkeypatch.setattr(stackctl, "run", run)
    return answer, calls


def test_reports_by_repo_and_caches(stack):
    answer, calls = stack
    answer(json.dumps({"frontends": [ROW]}))
    r = fu.frontend_updates(now=1000)
    assert r == {"checked": True, "frontends": {"demo": {k: ROW[k] for k in
                 ("current", "latest", "newer", "checked", "reason", "origin")}}}
    fu.frontend_updates(now=1000 + fu.CHECK_TTL - 1)
    assert calls == ["frontend_check"]
    fu.frontend_updates(now=1001, force=True)
    fu.frontend_updates(now=1000 + fu.CHECK_TTL + 1000)
    assert len(calls) == 3


def test_a_failed_check_is_quiet_and_retried_sooner(stack):
    answer, calls = stack
    answer(stackctl.StackError("timed out"))
    r = fu.frontend_updates(now=1000)
    assert not r["checked"] and r["frontends"] == {} and "timed out" in r["error"]
    fu.frontend_updates(now=1000 + fu.FAILURE_TTL + 1)
    assert len(calls) == 2


def test_off_runs_nothing(stack, monkeypatch):
    answer, calls = stack
    monkeypatch.setenv("STACK_UPDATE_CHECK", "off")
    assert fu.frontend_updates() == {"checked": False, "reason": "off", "frontends": {}}
    assert calls == []
