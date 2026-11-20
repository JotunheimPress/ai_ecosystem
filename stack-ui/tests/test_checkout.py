"""The dashboard finds the checkout whatever its folder is called (the GitHub repo is
ai_ecosystem; existing checkouts are ai-stack/)."""
import os
import subprocess
import sys
from pathlib import Path

APPCODE = Path(__file__).resolve().parents[1] / "appcode"
REPO = APPCODE.parents[1]


def _resolve(**env) -> list[str]:
    clean = {k: v for k, v in os.environ.items() if k not in ("STACK_CHECKOUT", "AI_ECOSYSTEM")}
    out = subprocess.run(
        [sys.executable, "-c", "from services import stackctl as s; print(s.CHECKOUT); print(s.ECO)"],
        cwd=APPCODE, env={**clean, **env}, capture_output=True, text=True, check=True)
    return out.stdout.split()


def test_running_from_a_checkout_uses_that_checkout():
    checkout, eco = _resolve()
    assert Path(checkout) == REPO and Path(eco) == REPO.parent


def test_stack_checkout_wins(tmp_path):
    checkout, eco = _resolve(STACK_CHECKOUT=str(tmp_path / "ai_ecosystem"))
    assert checkout == str(tmp_path / "ai_ecosystem") and eco == str(tmp_path)


def test_ai_ecosystem_sets_the_root_only(tmp_path):
    checkout, eco = _resolve(AI_ECOSYSTEM=str(tmp_path))
    assert Path(checkout) == REPO and eco == str(tmp_path)
