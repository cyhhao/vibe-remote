import json
import os
import pytest
import signal
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import paths
from vibe import runtime
from vibe import cli
from vibe import remote_access


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


def test_proc_cmdline_decode_preserves_argv_boundaries():
    command = runtime._decode_proc_cmdline(b"/tmp/Vibe Tools/cloudflared\x00tunnel\x00run\x00")

    assert command is not None
    assert shlex.split(command)[0] == "/tmp/Vibe Tools/cloudflared"


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


def test_remote_parser_accepts_pairing_command():
    parser = cli.build_parser()
    args = parser.parse_args(["remote", "pair", "vrp_test", "--device-name", "Mac Studio"])

    assert args.command == "remote"
    assert args.remote_command == "pair"
    assert args.pairing_key == "vrp_test"
    assert args.device_name == "Mac Studio"


def test_remote_parser_allows_guided_setup_without_subcommand():
    parser = cli.build_parser()
    args = parser.parse_args(["remote"])

    assert args.command == "remote"
    assert args.remote_command is None


def test_remote_parser_accepts_status_json():
    parser = cli.build_parser()
    args = parser.parse_args(["remote", "status", "--json"])

    assert args.command == "remote"
    assert args.remote_command == "status"
    assert args.json is True


def test_cmd_remote_pair_prompts_and_reports_success(monkeypatch, capsys):
    captured = {}

    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "vrp_prompt")

    def fake_pair(pairing_key: str, backend_url: str, device_name: str):
        captured.update(
            {
                "pairing_key": pairing_key,
                "backend_url": backend_url,
                "device_name": device_name,
            }
        )
        return {
            "ok": True,
            "public_url": "https://alex.avibe.bot",
            "running": True,
            "start": {"ok": True},
        }

    monkeypatch.setattr(remote_access, "pair", fake_pair)

    result = cli.cmd_remote_pair(
        SimpleNamespace(
            pairing_key=None,
            backend_url="https://backend.test",
            device_name="Mac Studio",
            json=False,
        )
    )

    assert result == 0
    assert captured == {
        "pairing_key": "vrp_prompt",
        "backend_url": "https://backend.test",
        "device_name": "Mac Studio",
    }
    output = capsys.readouterr().out
    assert "Remote access is ready" in output
    assert "https://alex.avibe.bot" in output
    assert "vibe remote status" in output


def test_cmd_remote_setup_explains_before_prompting_for_key(monkeypatch, capsys):
    events = []

    monkeypatch.setattr(remote_access, "status", lambda: {"ok": True, "paired": False})
    monkeypatch.setattr("builtins.input", lambda prompt: events.append(("ready", prompt)) or "")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: events.append(("key", prompt)) or "vrp_prompt")

    def fake_pair(pairing_key: str, backend_url: str, device_name: str):
        events.append(("pair", pairing_key, backend_url, device_name))
        return {
            "ok": True,
            "public_url": "https://alex.avibe.bot",
            "running": True,
            "start": {"ok": True},
        }

    monkeypatch.setattr(remote_access, "pair", fake_pair)

    result = cli.cmd_remote_setup(SimpleNamespace(remote_command=None))

    assert result == 0
    assert events == [
        ("ready", "Press Enter when you have copied the pairing key, or Ctrl+C to cancel."),
        ("key", "Paste pairing key (input hidden): "),
        ("pair", "vrp_prompt", "https://avibe.bot", "Vibe Remote"),
    ]
    output = capsys.readouterr().out
    assert "Open https://avibe.bot" in output
    assert "Create a new remote-access bot" in output
    assert "Copy the one-time pairing key" in output
    assert output.index("Open https://avibe.bot") < output.index("Pairing this device")


def test_cmd_remote_setup_shows_existing_pairing_without_prompt(monkeypatch, capsys):
    events = []

    monkeypatch.setattr(
        remote_access,
        "status",
        lambda: {
            "ok": True,
            "paired": True,
            "running": True,
            "public_url": "https://alex.avibe.bot",
        },
    )
    monkeypatch.setattr("builtins.input", lambda prompt: events.append(("ready", prompt)) or "")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: events.append(("key", prompt)) or "vrp_prompt")
    monkeypatch.setattr(remote_access, "pair", lambda *args, **kwargs: events.append(("pair", args, kwargs)))

    result = cli.cmd_remote_setup(SimpleNamespace(remote_command=None))

    assert result == 0
    assert events == []
    output = capsys.readouterr().out
    assert "Remote access is already configured." in output
    assert "https://alex.avibe.bot" in output
    assert "vibe remote pair" in output


def test_cmd_remote_pair_maps_invalid_key_to_user_action(monkeypatch, capsys):
    monkeypatch.setattr(
        remote_access,
        "pair",
        lambda *args, **kwargs: {"ok": False, "error": "invalid_pairing_key", "status": 400},
    )

    result = cli.cmd_remote_pair(
        SimpleNamespace(
            pairing_key="vrp_bad",
            backend_url="https://backend.test",
            device_name="Mac Studio",
            json=False,
        )
    )

    assert result == 1
    error_output = capsys.readouterr().err
    assert "Pairing key is invalid or expired." in error_output
    assert "https://avibe.bot" in error_output
    assert "vibe remote" in error_output


def test_cmd_remote_pair_missing_key_fails_without_request(monkeypatch, capsys):
    pair_calls = []

    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "")
    monkeypatch.setattr(remote_access, "pair", lambda *args, **kwargs: pair_calls.append(args))

    result = cli.cmd_remote_pair(
        SimpleNamespace(
            pairing_key=None,
            backend_url="https://backend.test",
            device_name="Mac Studio",
            json=False,
        )
    )

    assert result == 1
    assert pair_calls == []
    assert "missing pairing key" in capsys.readouterr().err


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
