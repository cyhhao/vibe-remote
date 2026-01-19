import json

from vibe import api


def test_detect_cli_prefers_claude_local(monkeypatch, tmp_path):
    claude_path = tmp_path / ".claude" / "local" / "claude"
    claude_path.parent.mkdir(parents=True, exist_ok=True)
    claude_path.write_text("#!/bin/sh\n")
    claude_path.chmod(0o755)

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)
    result = api.detect_cli("claude")
    assert result["found"] is True
    assert result["path"] == str(claude_path)
