"""Shared pytest fixtures for the Vibe Remote test suite.

Per AGENTS.md ("Tests and probes must never mutate the current local
environment or live user state"), every test runs against an isolated
data directory by default, so config writes, state files, and runtime
markers can never leak into the developer's real ``~/.vibe_remote/``
directory.

Historically a handful of install / upgrade tests mocked
``resolve_cli_path`` to return fixture paths like
``/Users/test/.nvm/.../codex`` but did not isolate the config directory.
The post-install bookkeeping in ``vibe.api._run_install_command`` then
called ``load_config()`` / ``cfg.save()`` against the real config.json and
persisted the fixture path, surfacing in the UI after the next restart.

Isolation mechanism: we set ``VIBE_REMOTE_HOME`` to a per-test tmp
directory. This means ``config.paths.get_vibe_remote_dir`` runs as
written — only its env-var-set branch is exercised under isolation, and
the function itself is never replaced, so the suite still catches
regressions in path-resolution logic.

Path-resolution tests (e.g. ``tests/test_v2_paths.py::test_paths_are_under_home``)
intentionally cover the env-var-unset branch where ``get_vibe_remote_dir``
falls back to ``Path.home() / '.vibe_remote'``. Those opt out with
``@pytest.mark.uses_real_paths``, run against the real environment, and
must remain read-only (they may not call ``cfg.save()`` or otherwise
write to ``~/.vibe_remote/``).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_vibe_remote_home(request, tmp_path, monkeypatch):
    if request.node.get_closest_marker("uses_real_paths"):
        return
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path / ".vibe_remote"))
