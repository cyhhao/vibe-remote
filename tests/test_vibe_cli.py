import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import patch

from config import paths
from vibe import runtime
from vibe import cli


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


def test_pid_alive_returns_true_on_permission_error(monkeypatch):
    monkeypatch.setattr(runtime.os, "name", "posix", raising=False)

    def _raise_permission(_pid, _sig):
        raise PermissionError()

    monkeypatch.setattr(runtime.os, "kill", _raise_permission)

    assert runtime.pid_alive(12345) is True


def test_pid_alive_delegates_to_windows_probe(monkeypatch):
    monkeypatch.setattr(runtime.os, "name", "nt", raising=False)
    monkeypatch.setattr(runtime, "_pid_alive_windows", lambda pid: pid == 4321)

    assert runtime.pid_alive(4321) is True
    assert runtime.pid_alive(1234) is False


def test_cli_pid_alive_reuses_runtime_impl(monkeypatch):
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid == 99)

    assert cli._pid_alive(99) is True
    assert cli._pid_alive(100) is False


def test_stop_process_delegates_to_windows_terminator(tmp_path, monkeypatch):
    pid_path = tmp_path / "service.pid"
    pid_path.write_text("4321", encoding="utf-8")

    monkeypatch.setattr(runtime.os, "name", "nt", raising=False)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid == 4321)
    monkeypatch.setattr(runtime, "_terminate_process_windows", lambda pid, timeout=5: pid == 4321)

    assert runtime.stop_process(pid_path) is True
    assert not pid_path.exists()


def test_cli_stop_process_reuses_runtime_impl(tmp_path, monkeypatch):
    pid_path = tmp_path / "service.pid"
    pid_path.write_text("123", encoding="utf-8")
    monkeypatch.setattr(runtime, "stop_process", lambda path: path == pid_path)

    assert cli._stop_process(pid_path) is True


def test_cli_stop_opencode_server_uses_runtime_helpers(tmp_path, monkeypatch):
    pid_file = tmp_path / "opencode_server.json"
    pid_file.write_text('{"pid": 321}', encoding="utf-8")

    monkeypatch.setattr(paths, "get_logs_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid == 321)
    monkeypatch.setattr(runtime, "get_process_command", lambda pid: "C:\\opencode.exe serve --port=4096")
    monkeypatch.setattr(runtime, "stop_pid", lambda pid, timeout=5: pid == 321)

    assert cli._stop_opencode_server() is True
    assert not pid_file.exists()


def test_stop_pid_handles_process_lookup_race(monkeypatch):
    monkeypatch.setattr(runtime.os, "name", "posix", raising=False)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: True)

    def _kill(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(runtime.os, "kill", _kill)

    assert runtime.stop_pid(12345) is False
