from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from config import paths
from vibe import runtime

# The supervisor lives in scripts/ (not an installed package); load it the same
# way test_incus_regression.py loads its sibling script.
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "incus_regression_supervisor.py"
SPEC = importlib.util.spec_from_file_location("incus_regression_supervisor", SCRIPT_PATH)
supervisor = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = supervisor
SPEC.loader.exec_module(supervisor)


def _write_restart_status(status: dict) -> None:
    path = runtime.get_restart_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_json(path, status)


def test_restart_in_progress_true_while_job_pid_alive(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    _write_restart_status({"ok": None, "state": "running", "supervisor_pid": 4242})
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid == 4242)

    assert supervisor._restart_in_progress() is True


def test_restart_in_progress_false_when_job_pid_dead(monkeypatch, tmp_path):
    # The P2: a killed restart job or a reboot leaves ok=None + state=running with
    # a now-dead pid. The supervisor must treat it as stale, not in progress, so
    # it can exit nonzero and let systemd recover instead of looping "restarting".
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    _write_restart_status({"ok": None, "state": "running", "supervisor_pid": 4242})
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: False)

    assert supervisor._restart_in_progress() is False


def test_restart_in_progress_false_without_recorded_pid(monkeypatch, tmp_path):
    # An older status with no job pid can't be confirmed alive → treat as stale.
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    _write_restart_status({"ok": None, "state": "scheduled"})

    assert supervisor._restart_in_progress() is False


def test_restart_in_progress_false_when_completed(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    _write_restart_status({"ok": True, "state": "succeeded", "supervisor_pid": 4242})
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: True)

    assert supervisor._restart_in_progress() is False
