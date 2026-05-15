"""Shared pytest fixtures for the Vibe Remote test suite.

Per AGENTS.md ("Tests and probes must never mutate the current local
environment or live user state"), every test runs against an isolated
data directory so config writes, state files, and runtime markers can
never leak into the developer's real ``~/.vibe_remote/`` directory.

Historically a handful of install / upgrade tests mocked
``resolve_cli_path`` to return fixture paths like
``/Users/test/.nvm/.../codex`` but did not isolate the config directory.
The post-install bookkeeping in ``vibe.api._run_install_command`` then
called ``load_config()`` / ``cfg.save()`` against the real config.json and
persisted the fixture path, surfacing in the UI after the next restart.

We monkeypatch ``paths.get_vibe_remote_dir`` rather than the underlying
``VIBE_REMOTE_HOME`` env var because the env-var fallback branch is
itself covered by the suite — ``test_v2_paths.py::test_paths_are_under_home``
asserts that, with ``VIBE_REMOTE_HOME`` unset, ``get_vibe_remote_dir()``
falls back to ``Path.home() / '.vibe_remote'``. Patching the function
keeps that fallback observable while still isolating writes, and the
``.vibe_remote`` suffix on the patched path keeps ``root.name`` matching
the production value so structural assertions on the path tree pass.
"""

from __future__ import annotations

import pytest

from config import paths


@pytest.fixture(autouse=True)
def _isolate_vibe_remote_home(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
