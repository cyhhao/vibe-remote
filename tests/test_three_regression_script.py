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


def test_three_regression_script_is_incus_only_wrapper() -> None:
    script = (REPO_ROOT / "scripts" / "run_three_regression.sh").read_text(encoding="utf-8")

    assert "scripts/incus_regression.py" in script
    assert "docker compose" not in script
    assert "--docker" not in script
    assert "THREE_REGRESSION_STATE_ROOT" not in script


def test_three_regression_script_maps_legacy_flags_to_incus() -> None:
    script = (REPO_ROOT / "scripts" / "run_three_regression.sh").read_text(encoding="utf-8")

    assert "--reset-config)" in script
    assert "incus_args+=(--reset-mode config)" in script
    assert "--reset-all|--reset-state)" in script
    assert "incus_args+=(--reset-mode all)" in script
    assert "--no-build)" in script
    assert "incus_args+=(--no-build-ui)" in script
