"""The credential store (services/credentials.py): prefixes, matching, the file it writes,
and git's helper protocol. No git or network: `test_frontend_install.py` drives it through
`stack` and real git."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "appcode"))

from services import credentials as c  # noqa: E402


@pytest.mark.parametrize("url, key, prefix", [
    ("https://github.com/El-Iguana/companion-assistant.git", "github.com/el-iguana/companion-assistant",
     "github.com/el-iguana"),
    ("https://GitHub.com/Owner/Repo/", "github.com/owner/repo", "github.com/owner"),
    ("https://user@gitlab.example.com:8443/group/sub/repo.git", "gitlab.example.com:8443/group/sub/repo",
     "gitlab.example.com:8443/group"),
    ("https://github.com:443/a/b", "github.com/a/b", "github.com/a"),
    ("https://example.com/repo.git", "example.com/repo", "example.com"),
    ("git@github.com:owner/repo", None, None),
    ("ssh://git@github.com/owner/repo", None, None),
    ("file:///srv/git/repo", None, None),
])
def test_keys_and_default_prefixes(url, key, prefix):
    assert c.key_of(url) == key
    assert c.prefix_for(url) == prefix


def test_longest_prefix_wins_and_a_prefix_matches_whole_segments(tmp_path):
    store = tmp_path / "credentials.json"
    c.save(store, "github.com", "host_token_1")
    c.save(store, "github.com/acme", "owner_token_2")
    c.save(store, "https://github.com/acme/special.git", "owner_again_4")  # a URL saves under its owner…
    c.save(store, "github.com/acme/special", "repo_token_3")   # …a prefix as given
    assert c.lookup("https://github.com/acme/special.git", store)[1] == "repo_token_3"
    assert c.lookup("https://github.com/ACME/other", store)[1] == "owner_again_4"
    assert c.lookup("https://github.com/acme-corp/x", store)[1] == "host_token_1"   # not acme/
    assert c.lookup("https://gitlab.com/acme/x", store) is None
    assert c.lookup("git@github.com:acme/x", store) is None


def test_the_file_is_private_versioned_and_replaced_whole(tmp_path):
    store = tmp_path / "sub" / "credentials.json"
    c.save(store, "gitlab.com", "glpat-12345678")
    assert oct(store.stat().st_mode & 0o777) == "0o600"
    doc = json.loads(store.read_text())
    assert doc["version"] == 1 and doc["credentials"]["gitlab.com"]["token"] == "glpat-12345678"
    assert list(store.parent.iterdir()) == [store]      # no temp file left behind
    assert c.lookup("https://gitlab.com/a/b", store) == ("oauth2", "glpat-12345678")
    c.save(store, "github.com/a", "tok_12345678", username="me")
    assert c.lookup("https://github.com/a/b", store) == ("me", "tok_12345678")
    assert c.remove(store, "gitlab.com") and not c.remove(store, "gitlab.com")
    assert [r["prefix"] for r in c.listing(store)] == ["github.com/a"]
    assert c.listing(store)[0]["hint"] == "…5678"


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"credentials": []}',
                                     '{"credentials": {"github.com": {"token": 5}}}'])
def test_an_unreadable_store_is_empty_not_an_error(tmp_path, content):
    store = tmp_path / "credentials.json"
    store.write_text(content)
    assert c.load(store) == {} and c.lookup("https://github.com/a/b", store) is None


@pytest.mark.parametrize("where, token", [
    ("github.com/a", "short"), ("github.com/a", "two words here"), ("github.com/a", "a\nb_123456"),
    ("not a prefix", "tok_12345678"), ("../etc", "tok_12345678"), ("github.com/a;b", "tok_12345678"),
])
def test_bad_tokens_and_prefixes_are_refused(tmp_path, where, token):
    with pytest.raises(c.CredentialError):
        c.save(tmp_path / "credentials.json", where, token)
    assert not (tmp_path / "credentials.json").exists()


def test_helper_answers_https_only_and_falls_back_to_the_old_token(tmp_path):
    store = tmp_path / "credentials.json"
    c.save(store, "github.com/acme", "owner_token_2")
    get = lambda fields, **env: c.helper_get(fields, store, env)  # noqa: E731
    gh = {"protocol": "https", "host": "github.com"}
    assert get(gh, STACK_GIT_URL="https://github.com/acme/x") == \
        "username=x-access-token\npassword=owner_token_2\n"
    assert get({**gh, "path": "acme/x.git"}) == "username=x-access-token\npassword=owner_token_2\n"
    # a hint for another host is ignored; then only the old token can answer
    assert get(gh, STACK_GIT_URL="https://gitlab.com/acme/x") == ""
    assert get(gh, STACK_GIT_URL="https://gitlab.com/acme/x", STACK_GIT_TOKEN="old_1") == \
        "username=x-access-token\npassword=old_1\n"
    assert get({"protocol": "https", "host": "example.com"}, STACK_GIT_TOKEN="old_1") == ""
    assert get({"protocol": "http", "host": "github.com"}, STACK_GIT_URL="https://github.com/acme/x") == ""


def test_used_dates_live_apart_from_the_tokens(tmp_path):
    """The helper marks a prefix used (once a day) in credentials-used.json, never rewriting
    the token file; listing shows it."""
    store = tmp_path / "credentials.json"
    c.save(store, "github.com/acme", "owner_token_2")
    before = store.read_text()
    out = c.helper_get({"protocol": "https", "host": "github.com"}, store,
                       {"STACK_GIT_URL": "https://github.com/acme/x"})
    assert "owner_token_2" in out
    assert store.read_text() == before
    assert c.used_path(store).name == "credentials-used.json"
    assert oct(c.used_path(store).stat().st_mode & 0o777) == "0o600"
    [row] = c.listing(store)
    assert row["used"] and row["checked"] == "" and row["check_ok"] is None
    # nothing matched, nothing marked; an unwritable directory is no error
    c.helper_get({"protocol": "https", "host": "gitlab.com"}, store, {})
    assert list(c.load_used(store)) == ["github.com/acme"]
    c.mark_used(tmp_path / "missing-dir" / "x" / "credentials.json", "a")


def test_annotate_records_a_test_but_never_touches_the_token(tmp_path):
    store = tmp_path / "credentials.json"
    c.save(store, "github.com/acme", "owner_token_2")
    c.annotate(store, "github.com/acme", checked="2026-11-27", check_ok=False,
               check_note="GitHub refuses it", expires="2026-12-01", token="evil_token_9")
    [row] = c.listing(store)
    assert (row["checked"], row["check_ok"], row["check_note"], row["expires"]) == (
        "2026-11-27", False, "GitHub refuses it", "2026-12-01")
    assert c.lookup("https://github.com/acme/x", store)[1] == "owner_token_2"
    c.annotate(store, "github.com/gone", checked="x")          # no entry: nothing written
    assert [r["prefix"] for r in c.listing(store)] == ["github.com/acme"]


def test_best_prefix():
    ps = ["github.com", "github.com/acme", "github.com/acme/special"]
    assert c.best_prefix("https://github.com/acme/special.git", ps) == "github.com/acme/special"
    assert c.best_prefix("https://github.com/acme-x/y", ps) == "github.com"
    assert c.best_prefix("https://gitlab.com/acme/y", ps) is None
    assert c.best_prefix("git@github.com:acme/y", ps) is None
