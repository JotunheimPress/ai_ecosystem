"""run-podman.sh on an NVIDIA host whose `ldconfig -p` lists libnvidia-ml early (jp-parent#53):
awk stopped reading at the match, ldconfig died of SIGPIPE, and pipefail + set -e ended the
script with exit 141 and no output, after the running dashboard had been removed. Runs the
real script against a fake podman, ldconfig and nvidia-smi; nothing is started.

    .venv/bin/python -m pytest -q tests/test_run_podman.py
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "run-podman.sh"


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


@pytest.fixture()
def host(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "podman.log"
    _exe(bin_ / "podman", f'echo "$*" >> {log}\ncase "$1 $2" in "image inspect") echo x;; esac\n')
    _exe(bin_ / "nvidia-smi", "exit 0\n")

    def run(ldconfig_body: str, gpu: str = "nvidia"):
        _exe(bin_ / "ldconfig", ldconfig_body)
        log.unlink(missing_ok=True)
        out = subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env={"HOME": os.environ["HOME"], "PATH": f"{bin_}:/usr/bin:/bin", "STACK_GPU": gpu,
                 "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")})
        runs = [ln for ln in log.read_text().splitlines() if ln.startswith("run -d")] \
            if log.exists() else []
        return out, runs

    return run


NVML = 'echo "\tlibnvidia-ml.so.1 (libc6,x86-64) => /usr/lib64/libnvidia-ml.so.1"\n'
TAIL = 'for i in $(seq 1 5000); do echo "\tlibfoo$i.so.1 (libc6,x86-64) => /usr/lib64/libfoo$i.so.1"; done\n'


def test_library_listed_early_still_starts_the_dashboard(host):
    out, runs = host(NVML + TAIL)                    # the match first, then a long listing
    assert out.returncode == 0, out.stderr
    assert len(runs) == 1 and "libnvidia-ml.so.1:ro" in runs[0]


def test_library_listed_late(host):
    out, runs = host(TAIL + NVML)
    assert out.returncode == 0, out.stderr
    assert len(runs) == 1 and "libnvidia-ml.so.1:ro" in runs[0]


def test_no_library_warns_and_starts_without_vram_readings(host):
    out, runs = host(TAIL)
    assert out.returncode == 0 and "libnvidia-ml.so.1 not found" in out.stderr
    assert len(runs) == 1 and "libnvidia-ml" not in runs[0]


def test_a_failure_says_where_it_stopped(host, tmp_path):
    # podman refusing the run: set -e ends the script, and the ERR trap names the line.
    _exe(tmp_path / "bin" / "podman", 'case "$1 $2" in "image inspect") echo x;; "run -d") exit 3;; esac\n')
    out, _ = host(NVML)
    assert out.returncode != 0 and "run-podman.sh stopped at line" in out.stderr
