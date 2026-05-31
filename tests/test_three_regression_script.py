from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_three_regression_script_has_valid_shell_syntax() -> None:
    subprocess.run(
        ["bash", "-n", str(REPO_ROOT / "scripts" / "run_three_regression.sh")],
        check=True,
        cwd=REPO_ROOT,
    )


def test_three_regression_compose_uses_canonical_state_root_env() -> None:
    compose = (REPO_ROOT / "docker-compose.three-regression.yml").read_text(encoding="utf-8")

    assert "${THREE_REGRESSION_STATE_ROOT:" in compose
    assert "./_tmp/three-regression" not in compose
    assert "VIBE_SHOW_RUNTIME_SOURCE" in compose
