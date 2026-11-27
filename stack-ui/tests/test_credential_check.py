"""The Credentials screen's server side: what a token covers, and what a test records. git
and GitHub are stand-ins here; the helper itself is tested against real git in
test_frontend_install.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "appcode"))

from services import credential_check as cc  # noqa: E402
from services import credentials, stackctl  # noqa: E402

FRONTS = [
    {"repo": "companion-assistant", "brand": "Cantinero",
     "origin": "https://github.com/El-Iguana/companion-assistant.git"},
    {"repo": "iguana_studio", "brand": "Iguana Studio",
     "origin": "https://github.com/JotunheimPress/iguana_studio.git"},
    {"repo": "mine", "brand": "Mine", "origin": "git@github.com:someone/mine"},
]


@pytest.fixture()
def store(monkeypatch, tmp_path):
    path = tmp_path / "credentials.json"
    monkeypatch.setattr(stackctl, "CREDENTIALS", path)
    monkeypatch.setattr(cc, "installed_origins", lambda: [dict(f) for f in FRONTS])
    credentials.save(path, "github.com/el-iguana", "tok_iguana_1")
    credentials.save(path, "gitlab.com", "glpat-12345678")
    return path


def test_clean_url_drops_what_was_written_into_an_origin():
    assert cc.clean_url("https://x-access-token:ghp_abc@github.com/a/b.git\n") == \
        "https://github.com/a/b.git"
    assert cc.clean_url("https://github.com/a/b") == "https://github.com/a/b"


def test_overview_says_what_each_token_covers_and_never_returns_one(store):
    doc = cc.overview()
    rows = {r["prefix"]: r for r in doc["credentials"]}
    assert rows["github.com/el-iguana"]["covers"] == ["companion-assistant"]
    assert rows["gitlab.com"]["covers"] == []
    fronts = {f["repo"]: f for f in doc["frontends"]}
    assert fronts["iguana_studio"]["prefix"] == "" and \
        fronts["iguana_studio"]["suggested"] == "github.com/jotunheimpress"
    assert fronts["mine"]["suggested"] == ""                 # ssh: a token can't help
    assert "tok_iguana_1" not in repr(doc) and "glpat-12345678" not in repr(doc)


def test_a_test_tries_each_covered_repo_and_github_and_records_it(store, monkeypatch):
    tried = []
    monkeypatch.setattr(cc, "_ls_remote", lambda url, s: tried.append(url) or (True, "can read it"))
    monkeypatch.setattr(cc, "_github", lambda tok: {"ok": True, "note": "GitHub accepts it",
                                                    "expires": "2026-12-20"})
    r = cc.test("github.com/el-iguana")
    assert tried == [FRONTS[0]["origin"]]
    assert r["passed"] is True and r["expires"] == "2026-12-20" and "companion-assistant" in r["note"]
    [row] = [x for x in credentials.listing(store) if x["prefix"] == "github.com/el-iguana"]
    assert row["check_ok"] is True and row["expires"] == "2026-12-20" and row["checked"]


def test_a_refused_token_fails_the_test(store, monkeypatch):
    monkeypatch.setattr(cc, "_ls_remote", lambda url, s: (False, "no access with this token"))
    monkeypatch.setattr(cc, "_github", lambda tok: {"ok": False, "expires": "",
                                                    "note": "GitHub refuses it: expired or revoked"})
    r = cc.test("github.com/el-iguana")
    assert r["passed"] is False and "expired" in r["note"]
    monkeypatch.setattr(cc, "_github", lambda tok: {"ok": True, "note": "ok", "expires": ""})
    r = cc.test("github.com/el-iguana")
    assert r["passed"] is False and r["note"] == "companion-assistant: no access with this token"


def test_a_token_nothing_uses_yet_is_inconclusive_off_github(store, monkeypatch):
    monkeypatch.setattr(cc, "_ls_remote", lambda url, s: pytest.fail("nothing to try"))
    r = cc.test("gitlab.com")
    assert r["passed"] is None and "nothing to try" in r["note"]


def test_testing_an_unknown_prefix_is_refused(store):
    with pytest.raises(KeyError):
        cc.test("github.com/nobody")


def test_removing_is_lan_only_and_only_what_is_saved(store):
    with pytest.raises(stackctl.StackError, match="local network"):
        stackctl.remove_credential("gitlab.com")
    tok = stackctl.REQUEST_LAN.set(True)
    try:
        with pytest.raises(stackctl.StackError, match="no token saved"):
            stackctl.remove_credential("github.com/nobody")
        stackctl.remove_credential("gitlab.com")
    finally:
        stackctl.REQUEST_LAN.reset(tok)
    assert [r["prefix"] for r in credentials.listing(store)] == ["github.com/el-iguana"]
