"""About's release check: version comparison, the cache, and what a private repo answers.
GitHub is never called."""
from __future__ import annotations

import io
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "appcode"))

from services import about, release_check  # noqa: E402


@pytest.mark.parametrize("latest, current, newer", [
    ("v1.1.0", "1.0.0", True),
    ("1.0.1", "1.0.0", True),
    ("v2.0", "1.9.9", True),
    ("v1.0.0", "1.0.0", False),
    ("v0.9.9", "1.0.0", False),
    ("v1.10.0", "1.9.0", True),     # numbers, not strings
    ("v1.1.0-rc1", "1.0.0", False),  # pre-releases are not offered
    ("nonsense", "1.0.0", False),
    ("v1.1.0", "unknown", False),
])
def test_is_newer(latest, current, newer):
    assert about.is_newer(latest, current) is newer


@pytest.fixture
def github(monkeypatch):
    """A stand-in for GitHub, counting calls; empty cache, checks on, no token, 1.0.0 here."""
    monkeypatch.setattr(release_check, "_cache", {})
    monkeypatch.setattr(release_check, "current_version", lambda: "1.0.0")
    for var in ("STACK_UPDATE_CHECK", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    from services import stackctl

    monkeypatch.setattr(stackctl, "CREDENTIALS", Path("/nonexistent/credentials.json"))
    calls = []

    def respond(body):
        def fake(_version):
            calls.append(1)
            if isinstance(body, Exception):
                raise body
            return body
        monkeypatch.setattr(release_check, "_fetch", fake)

    respond.calls = calls
    return respond


def _http(code):
    return urllib.error.HTTPError(release_check.LATEST_URL, code, "x", {}, io.BytesIO(b""))


def test_a_newer_release_is_reported(github):
    github({"tag_name": "v1.2.0", "html_url": "https://example/rel",
            "published_at": "2026-12-01T03:04:05Z"})
    assert release_check.latest_release(now=1000) == {
        "current": "1.0.0", "checked": True, "latest": "1.2.0", "newer": True,
        "url": "https://example/rel", "published": "2026-12-01"}


def test_the_same_release_is_not_newer(github):
    github({"tag_name": "v1.0.0"})
    r = release_check.latest_release(now=1000)
    assert r["checked"] and r["newer"] is False and r["url"] == about.RELEASES_URL


def test_answers_are_cached_and_force_asks_again(github):
    github({"tag_name": "v1.0.0"})
    release_check.latest_release(now=1000)
    release_check.latest_release(now=1000 + release_check.CHECK_TTL - 1)
    assert len(github.calls) == 1
    release_check.latest_release(now=1001, force=True)
    assert len(github.calls) == 2
    release_check.latest_release(now=1000 + release_check.CHECK_TTL + 1000)
    assert len(github.calls) == 3


def test_a_new_local_version_is_not_served_the_old_answer(github, monkeypatch):
    github({"tag_name": "v1.1.0"})
    assert release_check.latest_release(now=1000)["newer"] is True
    monkeypatch.setattr(release_check, "current_version", lambda: "1.1.0")
    assert release_check.latest_release(now=1001)["newer"] is False


def test_private_repo_without_a_token(github):
    github(_http(404))
    r = release_check.latest_release(now=1000)
    assert r["checked"] is False and r["reason"] == "private"


def test_a_token_without_access(github, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    github(_http(404))
    assert release_check.latest_release(now=1000)["reason"] == "token"


def test_offline_is_unreachable_and_cached_briefly(github):
    github(OSError("no route to host"))
    r = release_check.latest_release(now=1000)
    assert r["reason"] == "unreachable" and "no route" in r["error"]
    release_check.latest_release(now=1000 + release_check.FAILURE_TTL - 1)
    assert len(github.calls) == 1
    release_check.latest_release(now=1000 + release_check.FAILURE_TTL + 1)
    assert len(github.calls) == 2


def test_a_tag_that_isnt_a_version(github):
    github({"tag_name": "latest"})
    assert release_check.latest_release(now=1000)["reason"] == "unreachable"


@pytest.mark.parametrize("value", ["off", "0", "false", "NO"])
def test_checks_can_be_turned_off(github, monkeypatch, value):
    monkeypatch.setenv("STACK_UPDATE_CHECK", value)
    github({"tag_name": "v9.0.0"})
    assert release_check.latest_release() == {"current": "1.0.0", "checked": False, "reason": "off"}
    assert not github.calls


def test_the_token_goes_only_in_a_header(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        seen.update(url=req.full_url, auth=req.get_header("Authorization"))
        return Resp(b'{"tag_name": "v1.0.0"}')

    from services import stackctl

    monkeypatch.setattr(stackctl, "CREDENTIALS", Path("/nonexistent/credentials.json"))
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setattr(release_check.urllib.request, "urlopen", urlopen)
    release_check._fetch("1.0.0")
    assert seen == {"url": release_check.LATEST_URL, "auth": "Bearer secret"}
    assert "secret" not in seen["url"]


def test_a_saved_token_for_the_owner_beats_github_token(monkeypatch, tmp_path):
    from services import credentials, stackctl

    store = tmp_path / "credentials.json"
    credentials.save(store, f"https://github.com/{about.REPO}", "saved_token_1")
    monkeypatch.setattr(stackctl, "CREDENTIALS", store)
    monkeypatch.setenv("GITHUB_TOKEN", "old_token")
    assert release_check._token() == "saved_token_1"
    monkeypatch.setattr(stackctl, "CREDENTIALS", tmp_path / "missing.json")
    assert release_check._token() == "old_token"


def test_current_version_reads_the_stack_checkout():
    from services.stackctl import STACK

    want = (STACK.parent / "VERSION").read_text().strip() if (STACK.parent / "VERSION").exists() else "unknown"
    assert release_check.current_version() == want
