"""Docker smoke test for the README one-command install flow."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_IMAGE = os.environ.get("VIBE_INSTALL_TEST_IMAGE", "debian:trixie-slim")


def _resolve_install_wheel(fixtures_dir: Path) -> Path:
    configured_wheel = os.environ.get("VIBE_INSTALL_TEST_WHEEL")
    if configured_wheel:
        wheel_path = Path(configured_wheel).resolve()
        assert wheel_path.exists(), f"Expected install test wheel at {wheel_path}"
        return wheel_path

    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--wheel-dir", str(fixtures_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    wheels = sorted(fixtures_dir.glob("vibe_remote-*.whl"))
    assert wheels, "Expected a built wheel for install test"
    return wheels[-1]


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@pytest.mark.integration
def test_install_command_starts_vibe_in_fresh_container():
    if not _docker_available():
        pytest.skip("Docker is not available")

    with tempfile.TemporaryDirectory(prefix="vibe-install-fixtures-") as tmpdir:
        fixtures_dir = Path(tmpdir)
        wheel_path = _resolve_install_wheel(fixtures_dir)

        container_name = f"vibe-install-smoke-{os.getpid()}"
        command = (
            "apt-get update >/dev/null && "
            "apt-get install -y --no-install-recommends curl ca-certificates bash procps >/dev/null && "
            f"cat /work/install.sh | env VIBE_INSTALL_PACKAGE_SPEC=/fixtures/{wheel_path.name} bash && "
            "vibe version && "
            "vibe && sleep 2 && vibe status"
        )

        try:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--name",
                    container_name,
                    "--rm",
                    "-v",
                    f"{REPO_ROOT}:/work",
                    "-v",
                    f"{wheel_path.parent}:/fixtures",
                    "-w",
                    "/work",
                    BASE_IMAGE,
                    "bash",
                    "-lc",
                    command,
                ],
                capture_output=True,
                text=True,
                timeout=900,
            )
        finally:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Installing vibe command into" in result.stdout
    assert "vibe-remote installed successfully (from custom package spec)" in result.stdout
    assert "vibe-remote " in result.stdout
    assert "Web UI:" in result.stdout
    assert '"running": true' in result.stdout
    assert '"service_pid":' in result.stdout
