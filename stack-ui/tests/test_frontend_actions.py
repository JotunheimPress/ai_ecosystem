"""The dashboard's frontend actions: what stackctl lets the browser ask for. Install takes
only a name from the official list that isn't installed; update and remove only an
installed frontend; the catalog read survives `stack` failing."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "appcode"))

from services import stackctl  # noqa: E402

CATALOG = {"frontends": [
    {"repo": "iguana_studio", "installed": False, "official": False, "problems": []},
    {"repo": "nocturne", "installed": True, "official": True, "problems": []},
], "warnings": []}
INSTALLED = {"frontends": [{"repo": "nocturne"}, {"repo": "my-fork"}], "warnings": []}


@pytest.fixture()
def fake(monkeypatch):
    monkeypatch.setattr(stackctl, "catalog", lambda: CATALOG)
    monkeypatch.setattr(stackctl, "frontends", lambda force=False: INSTALLED)


def test_install_takes_a_listed_name_that_is_not_installed(fake):
    assert stackctl.resolve("frontend_add", "iguana_studio") == ["frontend", "add", "iguana_studio"]
    with pytest.raises(stackctl.StackError, match="already installed"):
        stackctl.resolve("frontend_add", "nocturne")
    for bad in ("my-fork", "https://github.com/x/y.git", "../etc", "iguana_studio; rm -rf ~"):
        with pytest.raises(stackctl.StackError, match="not an official frontend"):
            stackctl.resolve("frontend_add", bad)


@pytest.mark.parametrize("verb", ["frontend_update", "frontend_remove"])
def test_update_and_remove_take_an_installed_frontend(fake, verb):
    # Official or not: a frontend the user cloned themselves can be updated and removed too.
    for repo in ("nocturne", "my-fork"):
        assert stackctl.resolve(verb, repo)[-1] == repo
    for bad in ("iguana_studio", "stackui", "koboldcpp", "all", ""):
        with pytest.raises(stackctl.StackError):
            stackctl.resolve(verb, bad)


def test_frontend_verbs_are_mutating_and_streamed():
    for verb in ("frontend_add", "frontend_update", "frontend_remove"):
        v = stackctl.VERBS[verb]
        assert v.mutating and v.stream
    assert not stackctl.VERBS["frontend_catalog"].mutating


def test_catalog_reads_stack_and_survives_failure(monkeypatch):
    monkeypatch.setattr(stackctl, "run", lambda verb, target=None: {
        "stdout": json.dumps(CATALOG), "output": "", "exit_code": 0})
    assert stackctl.catalog()["frontends"][0]["repo"] == "iguana_studio"

    def boom(verb, target=None):
        raise stackctl.StackError("stack not found")
    monkeypatch.setattr(stackctl, "run", boom)
    assert stackctl.catalog() == {"frontends": [],
                                  "warnings": ["couldn't read the official frontend list"]}
    monkeypatch.setattr(stackctl, "run", lambda verb, target=None: {"stdout": "[1, 2]"})
    assert "malformed" in stackctl.catalog()["warnings"][0]


def test_stack_catalog_reaches_stack_only_when_set(monkeypatch):
    monkeypatch.delenv("STACK_CATALOG", raising=False)
    assert "STACK_CATALOG" not in stackctl._env()
    monkeypatch.setenv("STACK_CATALOG", "/tmp/list.json")
    assert stackctl._env()["STACK_CATALOG"] == "/tmp/list.json"


# ---- installing from a git URL: the local network only ---------------------------------

def _scope(client, host):
    return {"type": "http", "client": (client, 50000) if client else None,
            "headers": [(b"host", host.encode())] if host is not None else []}


@pytest.mark.parametrize("client, host", [
    ("127.0.0.1", "localhost:7863"), ("::1", "[::1]:7863"), ("::ffff:127.0.0.1", "localhost"),
    # Another machine on the LAN (Predator -> Halo), by address or by local name.
    ("192.168.1.20", "192.168.1.236:7863"), ("192.168.1.20", "amd-halo:7863"),
    ("192.168.1.20", "amd-halo.local:7863"), ("10.0.0.5", "AMD-Halo.Local."),
    ("172.16.4.2", "halo.lan"), ("192.168.1.20", "halo.home.arpa:7863"),
    ("fe80::1", "[fe80::2]:7863"), ("fd12::5", "halo.internal"),
    ("100.101.102.103", "amd-halo:7863"),     # Tailscale
])
def test_a_lan_client_asking_for_a_lan_name_is_lan(client, host):
    assert stackctl.is_lan_request(_scope(client, host))


@pytest.mark.parametrize("client, host", [
    ("81.2.69.160", "192.168.1.236:7863"),    # from the internet (a forwarded port)
    ("8.8.8.8", "amd-halo"),
    ("192.168.1.20", "evil.example:7863"),    # DNS rebinding: a site's own domain
    ("127.0.0.1", "evil.example"),
    ("192.168.1.20", "halo.example.com"), ("192.168.1.20", "8.8.8.8:7863"),
    ("192.168.1.20", "bad_name.local"), ("192.168.1.20", ""), ("127.0.0.1", None),
    ("testclient", "localhost"), (None, "localhost"),
])
def test_anything_else_is_not(client, host):
    assert not stackctl.is_lan_request(_scope(client, host))


@pytest.fixture()
def local():
    token = stackctl.REQUEST_LAN.set(True)
    yield
    stackctl.REQUEST_LAN.reset(token)


def test_url_install_is_refused_unless_the_request_is_from_the_lan():
    with pytest.raises(stackctl.StackError, match="local network"):
        stackctl.resolve("frontend_add_url", "https://github.com/someone/mine.git")


@pytest.mark.parametrize("url", [
    "https://github.com/someone/mine.git", "git@github.com:someone/mine",
    "ssh://git@example.com:2222/someone/mine.git", "file:///srv/git/mine",
])
def test_url_install_passes_a_plain_git_url_with_yes(local, url):
    assert stackctl.resolve("frontend_add_url", url) == ["frontend", "add", "--yes", url]


@pytest.mark.parametrize("url", [
    "ext::sh -c id", "-uhack:x/y", "http://example.com/a/mine.git", "https://x/a/mine;rm -rf ~",
    "https://x/a b", "mine", "", "https://x/" + "a" * 300,
])
def test_url_install_refuses_anything_else(local, url):
    with pytest.raises(stackctl.StackError):
        stackctl.resolve("frontend_add_url", url)


def test_the_job_thread_keeps_the_request_local(local, monkeypatch):
    """jobs.start resolves in the request; the worker resolves again in its own thread."""
    from services import jobs

    seen = []

    def stream(verb, target=None):
        seen.append(stackctl.resolve(verb, target))
        yield "exit", 0

    monkeypatch.setattr(stackctl, "stream", stream)
    job = jobs.start("frontend_add_url", "https://github.com/someone/mine.git")
    for _ in range(100):
        if job.done:
            break
        import time
        time.sleep(0.02)
    assert job.done and job.error is None and job.exit_code == 0
    assert seen == [["frontend", "add", "--yes", "https://github.com/someone/mine.git"]]


def test_saving_a_token_is_refused_unless_the_request_is_from_the_lan(monkeypatch, tmp_path):
    monkeypatch.setattr(stackctl, "CREDENTIALS", tmp_path / "credentials.json")
    with pytest.raises(stackctl.StackError, match="local network"):
        stackctl.save_credential("https://github.com/someone/mine.git", "github_pat_12345678")
    assert not (tmp_path / "credentials.json").exists()


def test_a_token_is_saved_under_the_urls_owner(local, monkeypatch, tmp_path):
    from services import credentials

    store = tmp_path / "credentials.json"
    monkeypatch.setattr(stackctl, "CREDENTIALS", store)
    assert stackctl.save_credential("https://github.com/Someone/mine.git",
                                    "github_pat_12345678") == "github.com/someone"
    assert credentials.lookup("https://github.com/someone/other", store) == (
        "x-access-token", "github_pat_12345678")
    assert oct(store.stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("url, token, why", [
    ("git@github.com:someone/mine", "github_pat_12345678", "https"),
    ("ssh://git@example.com/someone/mine.git", "github_pat_12345678", "https"),
    ("https://x/a/mine;rm -rf ~", "github_pat_12345678", "https"),
    ("https://github.com/someone/mine.git", "has a space in it", "token"),
    ("https://github.com/someone/mine.git", "line\nbreak_123", "token"),
    ("https://github.com/someone/mine.git", "", "token"),
])
def test_saving_a_token_refuses_anything_else(local, monkeypatch, tmp_path, url, token, why):
    monkeypatch.setattr(stackctl, "CREDENTIALS", tmp_path / "credentials.json")
    with pytest.raises(stackctl.StackError, match=why):
        stackctl.save_credential(url, token)
    assert not (tmp_path / "credentials.json").exists()
