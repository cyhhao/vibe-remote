from __future__ import annotations

import subprocess

from config import paths
from vibe import restart_supervisor
from vibe import runtime


def test_schedule_restart_spawns_supervisor_and_records_status(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    paths.get_runtime_pid_path().write_text("12345", encoding="utf-8")
    calls = {}

    monkeypatch.setattr(restart_supervisor, "get_restart_invocation_command", lambda vibe_path=None: ["/bin/vibe", "restart"])
    monkeypatch.setattr(restart_supervisor, "get_restart_environment", lambda vibe_path=None: {"PATH": "/bin"})
    monkeypatch.setattr(restart_supervisor, "get_safe_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(restart_supervisor, "_prune_restart_logs", lambda: None)

    def fake_popen(command, **kwargs):
        calls["command"] = command
        calls["kwargs"] = kwargs

        class Proc:
            pid = 45678

        return Proc()

    monkeypatch.setattr(restart_supervisor.subprocess, "Popen", fake_popen)

    result = restart_supervisor.schedule_restart(delay_seconds=60, vibe_path="/bin/vibe", trigger="agent")

    assert result["state"] == "scheduled"
    assert result["supervisor_pid"] == 45678
    assert result["old_pid"] == 12345
    assert calls["command"][:2] == ["/bin/vibe", "__restart-supervisor"]
    assert calls["command"][calls["command"].index("--delay-seconds") + 1] == "60"
    assert "--prepare-show-runtime" not in calls["command"]
    assert calls["kwargs"]["start_new_session"] is True
    assert calls["kwargs"]["env"] == {"PATH": "/bin"}
    assert runtime.read_json(runtime.get_restart_status_path())["job_id"] == result["job_id"]


def test_schedule_restart_can_prepare_show_runtime_after_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    calls = {}

    monkeypatch.setattr(restart_supervisor, "get_restart_invocation_command", lambda vibe_path=None: ["/bin/vibe", "restart"])
    monkeypatch.setattr(restart_supervisor, "get_restart_environment", lambda vibe_path=None: None)
    monkeypatch.setattr(restart_supervisor, "get_safe_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(restart_supervisor, "_prune_restart_logs", lambda: None)

    def fake_popen(command, **kwargs):
        calls["command"] = command

        class Proc:
            pid = 45678

        return Proc()

    monkeypatch.setattr(restart_supervisor.subprocess, "Popen", fake_popen)

    restart_supervisor.schedule_restart(delay_seconds=2, vibe_path="/bin/vibe", trigger="upgrade", prepare_show_runtime=True)

    assert "--prepare-show-runtime" in calls["command"]


def test_restart_job_stops_and_starts_service(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    paths.get_runtime_pid_path().write_text("111", encoding="utf-8")
    calls = []

    def stop_ui(timings=None, *, stop_remote_access=True):
        assert stop_remote_access is False
        calls.append("stop_ui")
        if timings is not None:
            timings["stop_remote_access_seconds"] = 0.01
        return True

    monkeypatch.setattr(runtime, "stop_ui", stop_ui)
    monkeypatch.setattr(runtime, "stop_service", lambda: calls.append("stop_service") or True)
    monkeypatch.setattr(restart_supervisor, "get_safe_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(restart_supervisor, "get_restart_invocation_command", lambda vibe_path=None: ["/bin/vibe", "restart"])
    monkeypatch.setattr(restart_supervisor, "get_restart_environment", lambda vibe_path=None: None)

    def fake_run(command, **kwargs):
        calls.append(("run", command))
        paths.get_runtime_pid_path().write_text("222", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(restart_supervisor.subprocess, "run", fake_run)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid == 222)
    monkeypatch.setattr(runtime, "service_pid_recorded", lambda pid: pid == 222)

    rc = restart_supervisor._run_restart_job(job_id="jobabc", delay_seconds=0, vibe_path="/bin/vibe", trigger="test")

    assert rc == 0
    assert calls == ["stop_ui", "stop_service", ("run", ["/bin/vibe", "start"])]
    status = runtime.read_json(runtime.get_restart_status_path())
    assert status["ok"] is True
    assert status["state"] == "succeeded"
    assert status["old_pid"] == 111
    assert status["new_pid"] == 222
    assert status["stage_durations"]["stop_remote_access_seconds"] == 0.01
    assert "stop_ui_total_seconds" in status["stage_durations"]
    assert "stop_service_seconds" in status["stage_durations"]
    assert "start_command_seconds" in status["stage_durations"]
    assert "restart_total_seconds" in status["stage_durations"]


def test_restart_job_prepares_show_runtime_after_service_start(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    paths.get_runtime_pid_path().write_text("111", encoding="utf-8")
    calls = []

    monkeypatch.setattr(runtime, "stop_ui", lambda timings=None, stop_remote_access=True: calls.append("stop_ui") or True)
    monkeypatch.setattr(runtime, "stop_service", lambda: calls.append("stop_service") or True)
    monkeypatch.setattr(restart_supervisor, "get_safe_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(restart_supervisor, "get_restart_invocation_command", lambda vibe_path=None: ["/bin/vibe", "restart"])
    monkeypatch.setattr(restart_supervisor, "get_restart_command", lambda vibe_path=None: ["/bin/vibe"])
    monkeypatch.setattr(restart_supervisor, "get_restart_environment", lambda vibe_path=None: None)

    def fake_run(command, **kwargs):
        calls.append(("run", command))
        if command == ["/bin/vibe", "start"]:
            paths.get_runtime_pid_path().write_text("222", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(restart_supervisor.subprocess, "run", fake_run)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid == 222)
    monkeypatch.setattr(runtime, "service_pid_recorded", lambda pid: pid == 222)

    rc = restart_supervisor._run_restart_job(
        job_id="jobruntime",
        delay_seconds=0,
        vibe_path="/bin/vibe",
        trigger="upgrade",
        prepare_show_runtime=True,
    )

    assert rc == 0
    assert calls == [
        "stop_ui",
        "stop_service",
        ("run", ["/bin/vibe", "start"]),
        ("run", ["/bin/vibe", "runtime", "prepare", "--strict"]),
    ]
    assert runtime.read_json(runtime.get_restart_status_path())["state"] == "succeeded"


def test_restart_job_aborts_when_stop_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    paths.get_runtime_pid_path().write_text("111", encoding="utf-8")
    calls = []

    monkeypatch.setattr(runtime, "stop_ui", lambda timings=None, stop_remote_access=True: calls.append("stop_ui") or True)
    monkeypatch.setattr(runtime, "stop_service", lambda: calls.append("stop_service") or False)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid == 111)
    monkeypatch.setattr(restart_supervisor, "get_safe_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(restart_supervisor.subprocess, "run", lambda *args, **kwargs: calls.append("run"))

    rc = restart_supervisor._run_restart_job(job_id="jobdef", delay_seconds=0, vibe_path="/bin/vibe", trigger="test")

    assert rc == 2
    assert calls == ["stop_ui", "stop_service"]
    status = runtime.read_json(runtime.get_restart_status_path())
    assert status["ok"] is False
    assert status["state"] == "failed"
    assert "did not stop" in status["error"]


def test_restart_job_continues_when_old_pid_already_exited(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    paths.get_runtime_pid_path().write_text("111", encoding="utf-8")
    calls = []

    monkeypatch.setattr(runtime, "stop_ui", lambda timings=None, stop_remote_access=True: calls.append("stop_ui") or True)
    monkeypatch.setattr(runtime, "stop_service", lambda: calls.append("stop_service") or False)
    monkeypatch.setattr(restart_supervisor, "get_safe_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(restart_supervisor, "get_restart_invocation_command", lambda vibe_path=None: ["/bin/vibe", "restart"])
    monkeypatch.setattr(restart_supervisor, "get_restart_environment", lambda vibe_path=None: None)

    def fake_run(command, **kwargs):
        calls.append(("run", command))
        paths.get_runtime_pid_path().write_text("222", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(restart_supervisor.subprocess, "run", fake_run)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid == 222)
    monkeypatch.setattr(runtime, "service_pid_recorded", lambda pid: pid == 222)

    rc = restart_supervisor._run_restart_job(job_id="joboldgone", delay_seconds=0, vibe_path="/bin/vibe", trigger="test")

    assert rc == 0
    assert calls == ["stop_ui", "stop_service", ("run", ["/bin/vibe", "start"])]
    assert runtime.read_json(runtime.get_restart_status_path())["state"] == "succeeded"


def test_restart_job_adopts_slow_starting_service_pid(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    paths.get_runtime_pid_path().write_text("111", encoding="utf-8")
    calls = []

    monkeypatch.setattr(runtime, "stop_ui", lambda timings=None, stop_remote_access=True: calls.append("stop_ui") or True)
    monkeypatch.setattr(runtime, "stop_service", lambda: calls.append("stop_service") or True)
    monkeypatch.setattr(restart_supervisor, "get_safe_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(restart_supervisor, "get_restart_invocation_command", lambda vibe_path=None: ["/bin/vibe", "restart"])
    monkeypatch.setattr(restart_supervisor, "get_restart_environment", lambda vibe_path=None: None)

    def fake_run(command, **kwargs):
        calls.append(("run", command))
        # Simulate `vibe start` returning while the worker is alive but has not
        # yet written runtime/vibe.pid.
        runtime.write_status("starting", "service process is still starting", 222, 333)
        try:
            paths.get_runtime_pid_path().unlink()
        except FileNotFoundError:
            pass
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(restart_supervisor.subprocess, "run", fake_run)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid == 222)
    monkeypatch.setattr(runtime, "service_pid_recorded", lambda pid: False)
    monkeypatch.setattr(runtime, "wait_for_service_pid", lambda pid, timeout: pid == 222)

    rc = restart_supervisor._run_restart_job(job_id="jobslow", delay_seconds=0, vibe_path="/bin/vibe", trigger="test")

    assert rc == 0
    status = runtime.read_json(runtime.get_restart_status_path())
    assert status["ok"] is True
    assert status["state"] == "succeeded"
    assert status["new_pid"] == 222
    service_status = runtime.read_status()
    assert service_status["state"] == "running"
    assert service_status["service_pid"] == 222
    assert service_status["ui_pid"] == 333


def test_restart_job_marks_start_timeout_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    paths.get_runtime_pid_path().write_text("111", encoding="utf-8")

    monkeypatch.setattr(runtime, "stop_ui", lambda timings=None, stop_remote_access=True: True)
    monkeypatch.setattr(runtime, "stop_service", lambda timings=None: True)
    monkeypatch.setattr(restart_supervisor, "get_safe_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(restart_supervisor, "get_restart_invocation_command", lambda vibe_path=None: ["/bin/vibe", "restart"])
    monkeypatch.setattr(restart_supervisor, "get_restart_environment", lambda vibe_path=None: None)
    monkeypatch.setattr(
        restart_supervisor.subprocess,
        "run",
        lambda command, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(command, 30)),
    )

    rc = restart_supervisor._run_restart_job(job_id="jobtimeout", delay_seconds=0, vibe_path="/bin/vibe", trigger="test")

    assert rc == 4
    status = runtime.read_json(runtime.get_restart_status_path())
    assert status["ok"] is False
    assert status["state"] == "failed"
    assert "timed out" in status["error"]
    assert "restart_total_seconds" in status["stage_durations"]
