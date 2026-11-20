"""`stack frontend available|add|update|remove` against a real git origin and a test
catalog (STACK_CATALOG), in a throwaway ecosystem (AI_ECOSYSTEM). podman is a fake that
only logs, so nothing here touches the machine's containers."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[2] / "stack"


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _exe(path: Path, body: str):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _manifest(**over):
    return {"repo": "demo", "brand": "Demo", "description": "A test frontend", "port": 7890,
            "health": "http://127.0.0.1:1/health", "container": "demo", "start": "run.sh",
            "build": "build.sh --quiet", **over}


@pytest.fixture()
def env(tmp_path):
    """An origin repo tagged v0.2.0 and v0.10.0, a catalog naming it, an empty ecosystem."""
    origin = tmp_path / "src" / "demo"     # named like the repo: stack warns otherwise
    origin.mkdir(parents=True)
    _git(origin, "init", "-q", "-b", "main")
    (origin / "frontend.json").write_text(json.dumps(_manifest()))
    _exe(origin / "run.sh", "exit 0\n")
    _exe(origin / "build.sh", 'echo "$1 $(git describe --tags --always)" >> built.log\n')
    (origin / ".gitignore").write_text("built.log\ndata/\n__pycache__/\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "one")
    _git(origin, "tag", "v0.2.0")
    _git(origin, "commit", "-qm", "two", "--allow-empty")
    _git(origin, "tag", "v0.10.0")
    _git(origin, "commit", "-qm", "unreleased", "--allow-empty")

    eco = tmp_path / "eco"
    eco.mkdir()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "podman.log"
    _exe(bin_ / "podman", f'echo "$*" >> {log}\n'
                          'case "$1 $2" in "container inspect") echo localhost/demo:latest;; esac\n'
                          'exit 0\n')
    catalog = tmp_path / "frontends.json"

    def write_catalog(**over):
        entry = {"repo": "demo", "url": f"file://{origin}", "brand": "Demo", "port": 7890,
                 "container": "demo", "needs": ["text"], **over}
        catalog.write_text(json.dumps({"version": 1, "frontends": [entry]}))

    write_catalog()

    def run(*args, check=True):
        out = subprocess.run(
            [str(STACK), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env={**os.environ, "AI_ECOSYSTEM": str(eco), "STACK_CATALOG": str(catalog),
                 "STACK_GPU": "none", "PATH": f"{bin_}:{os.environ['PATH']}"})
        if check:
            assert out.returncode == 0, out.stdout + out.stderr
        return out

    return dict(origin=origin, eco=eco, run=run, catalog=write_catalog, log=log)


def _available(env):
    return json.loads(env["run"]("frontend", "available", "--json").stdout)["frontends"]


def test_available_lists_the_catalog(env):
    [row] = _available(env)
    assert row["repo"] == "demo" and not row["installed"] and row["problems"] == []
    assert "available" in env["run"]("frontend", "available").stdout


def test_add_clones_the_newest_release_checks_and_builds(env):
    out = env["run"]("frontend", "add", "demo").stdout
    dest = env["eco"] / "demo"
    assert "release v0.10.0" in out and "installed demo" in out
    assert subprocess.run(["git", "-C", dest, "describe", "--tags"], capture_output=True,
                          text=True).stdout.strip() == "v0.10.0"
    assert (dest / "built.log").read_text().split() == ["--quiet", "v0.10.0"]
    assert not (env["eco"] / ".frontend-install").exists()
    assert "demo" in env["run"]("frontends").stdout
    [row] = _available(env)
    assert row["installed"] and row["dir"] == str(dest)
    again = env["run"]("frontend", "add", "demo", check=False)
    assert again.returncode != 0 and "already installed" in again.stderr


def test_add_refuses_a_clone_that_does_not_match_the_list(env):
    env["catalog"](port=7891)
    out = env["run"]("frontend", "add", "demo", check=False)
    assert out.returncode != 0 and "doesn't match the official list" in out.stdout
    assert "port" in out.stderr
    assert sorted(p.name for p in env["eco"].iterdir()) == []


@pytest.mark.parametrize("over, why", [
    ({"port": 7863}, "the stack's own"),
    ({"container": "stackui"}, "the stack's own"),
    ({"min_stack": "99.0.0"}, "needs AI Ecosystem 99.0.0"),
])
def test_add_refuses_clashes_before_cloning(env, over, why):
    env["catalog"](**over)
    out = env["run"]("frontend", "add", "demo", check=False)
    assert out.returncode != 0 and why in out.stderr
    assert list(env["eco"].iterdir()) == []


def test_add_refuses_a_port_an_installed_frontend_uses(env):
    other = env["eco"] / "other"
    other.mkdir()
    (other / "frontend.json").write_text(json.dumps(_manifest(repo="other", container="other",
                                                              build="")))
    _exe(other / "run.sh", "exit 0\n")
    out = env["run"]("frontend", "add", "demo", check=False)
    assert out.returncode != 0 and "port clash with other" in out.stderr


def test_bad_catalog_entries_are_skipped_with_a_warning(env):
    env["catalog"](url="http://example.com/demo.git")
    doc = json.loads(env["run"]("frontend", "available", "--json").stdout)
    assert doc["frontends"] == [] and "url must be https://" in doc["warnings"][0]


def test_update_moves_to_the_newest_release_and_rebuilds(env):
    env["run"]("frontend", "add", "demo")
    assert "up to date" in env["run"]("frontend", "update", "demo").stdout
    _git(env["origin"], "tag", "v0.11.0")
    out = env["run"]("frontend", "update", "demo").stdout
    assert "now at v0.11.0" in out
    assert (env["eco"] / "demo" / "built.log").read_text().split()[-1] == "v0.11.0"


def test_update_fast_forwards_a_working_clone(env):
    subprocess.run(["git", "clone", "-q", str(env["origin"]), str(env["eco"] / "demo")], check=True)
    _git(env["origin"], "commit", "-qm", "three", "--allow-empty")
    out = env["run"]("frontend", "update", "demo").stdout
    assert "pull --ff-only (main)" in out and "now at" in out


def test_remove_keeps_a_folder_with_user_data(env):
    env["run"]("frontend", "add", "demo")
    dest = env["eco"] / "demo"
    (dest / "data").mkdir()
    (dest / "data" / "save.db").write_text("x")
    (dest / "__pycache__").mkdir()
    out = env["run"]("frontend", "remove", "demo", check=False)
    assert out.returncode != 0 and "data/" in out.stdout and "__pycache__" not in out.stdout
    assert dest.exists() and "built.log" in out.stdout


def test_remove_stops_it_and_deletes_a_clean_clone(env):
    env["run"]("frontend", "add", "demo")
    dest = env["eco"] / "demo"
    (dest / "built.log").unlink()
    (dest / "__pycache__").mkdir()
    out = env["run"]("frontend", "remove", "demo").stdout
    assert "removed demo" in out and not dest.exists()
    log = env["log"].read_text()
    assert "rm -f demo" in log and "rmi localhost/demo:latest" in log


def test_build_with_shell_syntax_is_refused(env):
    other = env["eco"] / "other"
    other.mkdir()
    (other / "frontend.json").write_text(json.dumps(_manifest(repo="other", port=7895,
                                                              container="other",
                                                              build="run.sh; rm -rf ~")))
    _exe(other / "run.sh", "exit 0\n")
    doc = json.loads(env["run"]("frontends", "--json").stdout)
    assert doc["frontends"] == [] and "build must be a script" in doc["warnings"][0]


def test_official_means_cloned_from_the_listed_url(env):
    env["run"]("frontend", "add", "demo")
    [row] = _available(env)
    assert row["installed"] and row["official"]
    # Same repo name, cloned from somewhere else: installed, but not official.
    _git(env["eco"] / "demo", "remote", "set-url", "origin", "https://example.com/fork/demo.git")
    [row] = _available(env)
    assert row["installed"] and not row["official"]


def _check(env):
    return {r["repo"]: r for r in json.loads(env["run"]("frontend", "check", "--json").stdout)["frontends"]}


def test_check_reports_a_newer_release_until_update_takes_it(env):
    env["run"]("frontend", "add", "demo")
    row = _check(env)["demo"]
    assert row == {"repo": "demo", "current": "v0.10.0", "latest": "v0.10.0", "newer": False,
                   "checked": True, "reason": ""}
    _git(env["origin"], "tag", "-a", "v0.11.0", "-m", "annotated")   # peeled to its commit
    row = _check(env)["demo"]
    assert row["newer"] and row["latest"] == "v0.11.0" and row["current"] == "v0.10.0"
    assert "v0.11.0 is out" in env["run"]("frontend", "check").stdout
    head = subprocess.run(["git", "-C", env["eco"] / "demo", "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout
    assert not (env["eco"] / "demo" / "FETCH_HEAD").exists() and head    # check fetched nothing
    env["run"]("frontend", "update", "demo")
    assert not _check(env)["demo"]["newer"]


def test_check_a_working_clone_past_the_newest_tag_is_up_to_date(env):
    subprocess.run(["git", "clone", "-q", str(env["origin"]), str(env["eco"] / "demo")], check=True)
    row = _check(env)["demo"]
    assert row["latest"] == "v0.10.0" and not row["newer"]


def test_check_without_releases_or_origin_reports_nothing_newer(env, tmp_path):
    bare = tmp_path / "eco" / "demo"
    bare.mkdir()
    (bare / "frontend.json").write_text(json.dumps(_manifest()))
    _exe(bare / "run.sh", "exit 0\n")
    _exe(bare / "build.sh", "exit 0\n")
    _git(bare, "init", "-q")
    row = _check(env)["demo"]
    assert not row["newer"] and not row["checked"] and row["reason"] == "no origin"
    _git(bare, "remote", "add", "origin", str(tmp_path / "nowhere"))
    row = _check(env)["demo"]
    assert not row["newer"] and row["reason"] == "unreachable"
