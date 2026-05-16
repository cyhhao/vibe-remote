from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibe import api


def test_restart_backend_reports_claude_failure_when_controller_unacknowledged(monkeypatch) -> None:
    monkeypatch.setattr(api, "_request_controller_restart", lambda name: (False, None))

    result = api.restart_backend("claude")

    assert result["ok"] is False
    assert "not acknowledged" in result["message"]
