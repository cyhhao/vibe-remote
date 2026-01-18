import json
import os
import signal
import sys
from pathlib import Path

from config import paths
from vibe import runtime


def test_default_config_written(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    runtime.ensure_dirs()
    config = runtime.ensure_config()
    assert config.mode == "self_host"
    assert (tmp_path / ".vibe_remote" / "config" / "config.json").exists()


def test_status_written(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    runtime.ensure_dirs()
    runtime.write_status("running", detail="pid=123")
    payload = json.loads(paths.get_runtime_status_path().read_text(encoding="utf-8"))
    assert payload["state"] == "running"
    assert payload["detail"] == "pid=123"


def test_stop_process_handles_missing_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    runtime.ensure_dirs()
    assert runtime.stop_process(paths.get_runtime_pid_path()) is False
