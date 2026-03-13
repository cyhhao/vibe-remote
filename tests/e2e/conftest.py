"""E2E test fixtures: manage Docker container lifecycle."""

import os
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


def _url(path: str) -> str:
    return f"{E2E_BASE_URL}{path}"


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
    compose_file = os.path.abspath(COMPOSE_FILE)

    # Build and start
    env = os.environ.copy()
    env["VIBE_E2E_PORT"] = str(E2E_PORT)

    subprocess.run(
        ["docker", "compose", "-f", compose_file, "build"],
        check=True,
        env=env,
        capture_output=True,
    )
    subprocess.run(
        ["docker", "compose", "-f", compose_file, "up", "-d"],
        check=True,
        env=env,
        capture_output=True,
    )

    # Wait for healthy
    if not _wait_for_healthy(timeout=60):
        # Dump logs for debugging
        logs = subprocess.run(
            ["docker", "compose", "-f", compose_file, "logs"],
            capture_output=True,
            text=True,
            env=env,
        )
        pytest.fail(
            f"Vibe container did not become healthy within 60s.\nSTDOUT:\n{logs.stdout}\nSTDERR:\n{logs.stderr}"
        )

    yield E2E_BASE_URL

    # Teardown
    subprocess.run(
        ["docker", "compose", "-f", compose_file, "down", "-v"],
        env=env,
        capture_output=True,
    )


@pytest.fixture(scope="session")
def api_url(vibe_container):
    """Convenience: returns the base URL string."""
    return vibe_container
