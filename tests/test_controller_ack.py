from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibe import api


def test_ack_success_returns_no_error(tmp_path: Path) -> None:
    """Marker absent + no .err sentinel → handled=True, error=None."""
    marker = tmp_path / "restart-opencode.cmd"
    # marker never existed → already "removed"
    handled, error = api._wait_for_controller_ack(marker, timeout=0.05)
    assert handled is True
    assert error is None


def test_ack_failure_reads_and_consumes_err_sentinel(tmp_path: Path) -> None:
    """Marker absent + .err present → handled=True, error=<message>, sentinel cleaned."""
    marker = tmp_path / "restart-opencode.cmd"
    err_marker = tmp_path / "restart-opencode.cmd.err"
    err_marker.write_text("transport teardown failed", encoding="utf-8")

    handled, error = api._wait_for_controller_ack(marker, timeout=0.05)
    assert handled is True
    assert error == "transport teardown failed"
    # Sentinel must be consumed so the next restart starts clean.
    assert not err_marker.exists()


def test_ack_timeout_leaves_marker(tmp_path: Path) -> None:
    """Marker still present after timeout → handled=False, caller falls back to direct kill."""
    marker = tmp_path / "restart-opencode.cmd"
    marker.write_text("{}", encoding="utf-8")
    handled, error = api._wait_for_controller_ack(marker, timeout=0.05)
    assert handled is False
    assert error is None
    # The watcher would normally delete this; the caller does its own cleanup.
    assert marker.exists()


def test_ack_blank_err_falls_back_to_unknown(tmp_path: Path) -> None:
    """An empty .err shouldn't masquerade as success — surface a generic message."""
    marker = tmp_path / "restart-opencode.cmd"
    err_marker = tmp_path / "restart-opencode.cmd.err"
    err_marker.write_text("", encoding="utf-8")

    handled, error = api._wait_for_controller_ack(marker, timeout=0.05)
    assert handled is True
    assert error == "unknown error"
    assert not err_marker.exists()
