import json
import os
import pytest
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


def test_cmd_restart_schedules_delayed_restart(monkeypatch, capsys):
    scheduled = {}
    stop_called = []
    start_called = []

    monkeypatch.setattr(cli, "cache_running_vibe_path", lambda: "/usr/local/bin/vibe")
    monkeypatch.setattr(cli, "get_restart_command", lambda vibe_path=None: [vibe_path or "vibe"])
    monkeypatch.setattr(cli, "get_restart_environment", lambda vibe_path=None: None)
    monkeypatch.setattr(cli, "get_safe_cwd", lambda: "/tmp")
    monkeypatch.setattr(
        cli.api,
        "_spawn_delayed_restart",
        lambda command, cwd, delay_seconds=2.0, env=None: scheduled.update(
            {"command": command, "cwd": cwd, "delay_seconds": delay_seconds, "env": env}
        ),
    )
    monkeypatch.setattr(cli, "cmd_stop", lambda: stop_called.append(True))
    monkeypatch.setattr(cli, "cmd_vibe", lambda: start_called.append(True))

    assert cli._cmd_restart_with_delay(60) == 0
    assert scheduled == {
        "command": ["/usr/local/bin/vibe", "restart"],
        "cwd": "/tmp",
        "delay_seconds": 60,
        "env": None,
    }
    assert stop_called == []
    assert start_called == []

    output = capsys.readouterr().out
    assert "Restart scheduled in 1 minute." in output
    assert "delayed restart will run in the background" in output


def test_cmd_restart_schedules_delayed_restart_with_import_env(monkeypatch):
    scheduled = {}

    monkeypatch.setattr(cli, "cache_running_vibe_path", lambda: None)
    monkeypatch.setattr(
        cli,
        "get_restart_command",
        lambda vibe_path=None: [sys.executable, "-c", "from vibe.cli import main; main()"],
    )
    monkeypatch.setattr(cli, "get_restart_environment", lambda vibe_path=None: {"PYTHONPATH": "/repo"})
    monkeypatch.setattr(cli, "get_safe_cwd", lambda: "/tmp")
    monkeypatch.setattr(
        cli.api,
        "_spawn_delayed_restart",
        lambda command, cwd, delay_seconds=2.0, env=None: scheduled.update(
            {"command": command, "cwd": cwd, "delay_seconds": delay_seconds, "env": env}
        ),
    )

    assert cli._cmd_restart_with_delay(5) == 0
    assert scheduled == {
        "command": [sys.executable, "-c", "from vibe.cli import main; main()", "restart"],
        "cwd": "/tmp",
        "delay_seconds": 5,
        "env": {"PYTHONPATH": "/repo"},
    }


def test_cmd_restart_runs_synchronously_by_default(monkeypatch):
    calls = []

    monkeypatch.setattr(cli, "cmd_stop", lambda: calls.append("stop") or 0)
    monkeypatch.setattr(cli, "cmd_vibe", lambda: calls.append("start") or 0)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))

    assert cli._cmd_restart_with_delay(0) == 0
    assert calls == ["stop", ("sleep", 3), "start"]


def test_restart_parser_accepts_delay_seconds():
    parser = cli.build_parser()
    args = parser.parse_args(["restart", "--delay-seconds", "60"])

    assert args.command == "restart"
    assert args.delay_seconds == 60


@pytest.mark.parametrize("raw_value", ["nan", "inf", "-inf"])
def test_restart_parser_rejects_non_finite_delay_seconds(raw_value):
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["restart", "--delay-seconds", raw_value])


def test_stop_pid_handles_process_lookup_race(monkeypatch):
    monkeypatch.setattr(runtime.os, "name", "posix", raising=False)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: True)

    def _kill(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(runtime.os, "kill", _kill)

    assert runtime.stop_pid(12345) is False


def test_stop_pid_handles_permission_error(monkeypatch):
    monkeypatch.setattr(runtime.os, "name", "posix", raising=False)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: True)

    def _kill(pid, sig):
        raise PermissionError()

    monkeypatch.setattr(runtime.os, "kill", _kill)

    assert runtime.stop_pid(12345) is False
