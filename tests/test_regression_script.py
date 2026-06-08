from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_regression_script_has_valid_shell_syntax() -> None:
    subprocess.run(
        ["bash", "-n", str(REPO_ROOT / "scripts" / "run_regression.sh")],
        check=True,
        cwd=REPO_ROOT,
    )
    subprocess.run(
        ["bash", "-n", str(REPO_ROOT / "scripts" / "run_three_regression.sh")],
        check=True,
        cwd=REPO_ROOT,
    )


def test_regression_script_is_incus_only_wrapper() -> None:
    script = (REPO_ROOT / "scripts" / "run_regression.sh").read_text(encoding="utf-8")

    assert "scripts/incus_regression.py" in script
    assert "docker compose" not in script
    assert "--docker" not in script
    assert "REGRESSION_STATE_ROOT" not in script


def test_regression_script_maps_legacy_flags_to_incus() -> None:
    script = (REPO_ROOT / "scripts" / "run_regression.sh").read_text(encoding="utf-8")

    assert "--reset-config)" in script
    assert "incus_args+=(--reset-mode config)" in script
    assert "--reset-all|--reset-state)" in script
    assert "incus_args+=(--reset-mode all)" in script
    assert "--no-build)" in script
    assert "incus_args+=(--no-build-ui)" in script


def test_legacy_three_regression_script_delegates_to_new_wrapper() -> None:
    script = (REPO_ROOT / "scripts" / "run_three_regression.sh").read_text(encoding="utf-8")

    assert 'exec "$SCRIPT_DIR/run_regression.sh" "$@"' in script


def test_regression_maintenance_commands_accept_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.regression"
    env_file.write_text("REGRESSION_PORT=15999\n", encoding="utf-8")

    subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "run_three_regression.sh"),
            "--status",
            "--env-file",
            str(env_file),
            "--dry-run",
        ],
        check=True,
        cwd=REPO_ROOT,
    )


def test_regression_wrapper_forwards_worktree_port_range() -> None:
    subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "run_regression.sh"),
            "--worktree",
            "--slug",
            "demo-branch",
            "--worktree-port-start",
            "15240",
            "--worktree-port-end",
            "15240",
            "--dry-run",
            "--no-build-ui",
        ],
        check=True,
        cwd=REPO_ROOT,
    )
