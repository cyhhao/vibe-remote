"""Shared pytest fixtures for the Vibe Remote test suite.

Per AGENTS.md ("Tests and probes must never mutate the current local
environment or live user state"), every test runs against an isolated
``VIBE_REMOTE_HOME`` so config writes, state files, and runtime markers can
never leak into the developer's real ``~/.vibe_remote/`` directory.

Historically a handful of install / upgrade tests mocked
``resolve_cli_path`` to return fixture paths like
``/Users/test/.nvm/.../codex`` but did not isolate the config directory.
The post-install bookkeeping in ``vibe.api._run_install_command`` then
called ``load_config()`` / ``cfg.save()`` against the real config.json and
persisted the fixture path, surfacing in the UI after the next restart.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_vibe_remote_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path / "vibe_remote_home"))
