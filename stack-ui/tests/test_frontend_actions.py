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
