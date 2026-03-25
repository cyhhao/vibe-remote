from __future__ import annotations

import logging
from pathlib import Path

import main
from vibe import runtime


def test_build_logging_handlers_excludes_stdout_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_DISABLE_STDOUT_LOGGING", "1")

    handlers = main._build_logging_handlers(str(tmp_path))

    assert len(handlers) == 1
    assert isinstance(handlers[0], logging.FileHandler)


def test_build_logging_handlers_keeps_stdout_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("VIBE_DISABLE_STDOUT_LOGGING", raising=False)

    handlers = main._build_logging_handlers(str(tmp_path))

    assert len(handlers) == 2
    assert isinstance(handlers[0], logging.StreamHandler)
    assert isinstance(handlers[1], logging.FileHandler)


def test_start_service_disables_stdout_logging_for_background_process(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    pid_path = tmp_path / "vibe.pid"

    monkeypatch.setattr(runtime.paths, "get_runtime_pid_path", lambda: pid_path)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: False)
    monkeypatch.setattr(runtime, "get_service_main_path", lambda: Path("/tmp/main.py"))

    def fake_spawn_background(args, pid_path_arg, stdout_name, stderr_name, env=None):
        captured["args"] = args
        captured["pid_path"] = pid_path_arg
        captured["stdout_name"] = stdout_name
        captured["stderr_name"] = stderr_name
        captured["env"] = env
        return 12345

    monkeypatch.setattr(runtime, "spawn_background", fake_spawn_background)

    pid = runtime.start_service()

    assert pid == 12345
    assert captured["pid_path"] == pid_path
    assert captured["stdout_name"] == "service_stdout.log"
    assert captured["stderr_name"] == "service_stderr.log"
    assert isinstance(captured["env"], dict)
    assert captured["env"]["VIBE_DISABLE_STDOUT_LOGGING"] == "1"
