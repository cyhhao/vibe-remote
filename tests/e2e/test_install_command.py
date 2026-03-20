"""Docker smoke test for the README one-command install flow."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "install.sh"
BASE_IMAGE = os.environ.get("VIBE_INSTALL_TEST_IMAGE", "debian:trixie-slim")


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

    container_name = f"vibe-install-smoke-{os.getpid()}"
    command = (
        "apt-get update >/dev/null && "
        "apt-get install -y --no-install-recommends curl ca-certificates bash procps >/dev/null && "
        "cat /work/install.sh | bash && "
        "vibe && sleep 2 && vibe status"
    )

    result = subprocess.run(
        [
            "docker",
            "run",
            "--name",
            container_name,
            "--rm",
            "-v",
            f"{REPO_ROOT}:/work",
            "-w",
            "/work",
            BASE_IMAGE,
            "bash",
            "-lc",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Installing vibe command into" in result.stdout
    assert "Web UI:" in result.stdout
    assert '"running": true' in result.stdout
    assert '"service_pid":' in result.stdout
