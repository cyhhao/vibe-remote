"""E2E test fixtures: manage Docker container lifecycle."""

import os
import shutil
import subprocess
import time

import pytest
import urllib.request
import urllib.error

# E2E tests connect to the Vibe container on this port
E2E_PORT = int(os.environ.get("VIBE_E2E_PORT", "15123"))
E2E_BASE_URL = f"http://127.0.0.1:{E2E_PORT}"

# Compose file path (relative to repo root)
COMPOSE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "docker-compose.e2e.yml")

# When true, skip container teardown (set by run_e2e.sh --keep)
KEEP_CONTAINER = os.environ.get("VIBE_E2E_KEEP", "false").lower() == "true"


def _docker_available() -> bool:
    """Check if Docker CLI is installed and the daemon is reachable."""
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _url(path: str) -> str:
    return f"{E2E_BASE_URL}{path}"


def _compose_env() -> dict:
    env = os.environ.copy()
    env["VIBE_E2E_PORT"] = str(E2E_PORT)
    return env


def _compose_down(compose_file: str, env: dict) -> None:
    subprocess.run(
        ["docker", "compose", "-f", compose_file, "down", "-v"],
        env=env,
        capture_output=True,
    )


def _wait_for_healthy(timeout: int = 60) -> bool:
    """Poll /health until it responds or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = urllib.request.urlopen(_url("/health"), timeout=3)
            if resp.status == 200:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    return False


@pytest.fixture(scope="session")
def vibe_container():
    """Start Vibe container for the entire test session, tear down after."""
    if not _docker_available():
        pytest.skip("Docker is not available — skipping E2E tests")

    compose_file = os.path.abspath(COMPOSE_FILE)
    env = _compose_env()

    try:
        subprocess.run(
            ["docker", "compose", "-f", compose_file, "build"],
            check=True,
            env=env,
        )
        subprocess.run(
            ["docker", "compose", "-f", compose_file, "up", "-d"],
            check=True,
            env=env,
        )

        # Wait for healthy
        if not _wait_for_healthy(timeout=60):
            logs = subprocess.run(
                ["docker", "compose", "-f", compose_file, "logs"],
                capture_output=True,
                text=True,
                env=env,
            )
            pytest.fail(
                f"Vibe container did not become healthy within 60s.\nSTDOUT:\n{logs.stdout}\nSTDERR:\n{logs.stderr}"
            )
    except Exception:
        # Always clean up on startup failure
        _compose_down(compose_file, env)
        raise

    yield E2E_BASE_URL

    # Teardown (skip if --keep)
    if not KEEP_CONTAINER:
        _compose_down(compose_file, env)


@pytest.fixture(scope="session")
def api_url(vibe_container):
    """Convenience: returns the base URL string."""
    return vibe_container
