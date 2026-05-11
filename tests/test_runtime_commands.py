from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.runtime_commands import RuntimeCommandWatcher


def _make_watcher(tmp_path: Path, handler) -> tuple[RuntimeCommandWatcher, Path]:
    """Build a watcher whose controller's auth service exposes ``handler``."""
    auth_service = SimpleNamespace(_refresh_backend_runtime=handler)
    controller = SimpleNamespace(agent_auth_service=auth_service)
    watcher = RuntimeCommandWatcher(controller, directory=tmp_path)  # type: ignore[arg-type]
    marker = tmp_path / "restart-opencode.cmd"
    marker.write_text('{"backend":"opencode"}', encoding="utf-8")
    return watcher, marker


def test_handle_restart_success_deletes_marker_no_err(tmp_path: Path) -> None:
    """Happy path: marker deleted, no ``.err`` companion."""

    async def handler(name: str) -> None:
        assert name == "opencode"

    watcher, marker = _make_watcher(tmp_path, handler)
    asyncio.run(watcher._handle_restart("opencode", marker))

    assert not marker.exists()
    assert not (tmp_path / "restart-opencode.cmd.err").exists()


def test_handle_restart_failure_writes_err_sentinel(tmp_path: Path) -> None:
    """Handler raises → marker deleted AND ``.err`` companion written."""

    async def handler(name: str) -> None:
        raise RuntimeError("transport teardown failed mid-way")

    watcher, marker = _make_watcher(tmp_path, handler)
    asyncio.run(watcher._handle_restart("opencode", marker))

    assert not marker.exists()
    err = tmp_path / "restart-opencode.cmd.err"
    assert err.exists()
    assert "transport teardown failed mid-way" in err.read_text()


def test_handle_restart_missing_handler_writes_err_sentinel(tmp_path: Path) -> None:
    """No ``_refresh_backend_runtime`` on the service → also surface failure."""
    auth_service = SimpleNamespace()  # no handler attribute
    controller = SimpleNamespace(agent_auth_service=auth_service)
    watcher = RuntimeCommandWatcher(controller, directory=tmp_path)  # type: ignore[arg-type]
    marker = tmp_path / "restart-codex.cmd"
    marker.write_text("{}", encoding="utf-8")

    asyncio.run(watcher._handle_restart("codex", marker))

    assert not marker.exists()
    err = tmp_path / "restart-codex.cmd.err"
    assert err.exists()
    assert "refresh handler unavailable" in err.read_text()


def test_handle_restart_err_message_truncated(tmp_path: Path) -> None:
    """Pathological multi-MB exception messages must not blow up the disk."""

    async def handler(name: str) -> None:
        raise RuntimeError("x" * 5000)

    watcher, marker = _make_watcher(tmp_path, handler)
    asyncio.run(watcher._handle_restart("opencode", marker))

    err = tmp_path / "restart-opencode.cmd.err"
    assert err.exists()
    assert len(err.read_text()) <= 1024
