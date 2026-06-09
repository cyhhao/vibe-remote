#!/usr/bin/env python3
"""Run Avibe service + Web UI inside the Incus regression systemd service."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from config import paths
from config.v2_config import V2Config
from vibe import runtime


def _read_pid_file(pid_path: Path) -> int | None:
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def _reap_child(pid: int | None) -> None:
    if not isinstance(pid, int) or pid <= 0 or os.name == "nt":
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return
    except OSError:
        return


def _restart_in_progress() -> bool:
    status = runtime.read_json(runtime.get_restart_status_path()) or {}
    # Only the active stop/start phase ("running") should suppress the supervisor's
    # own recovery. A "scheduled" (delayed) restart is just sleeping and hasn't
    # touched the service yet, so a crash during the delay must still be recovered
    # immediately rather than waiting for the job to wake.
    if status.get("ok") is not None or status.get("state") != "running":
        return False
    # And only while the job process is still alive: a stale status left by a
    # killed restart job or a reboot would otherwise keep this true forever, so the
    # supervisor would loop writing "restarting" instead of exiting nonzero to let
    # systemd recover the service.
    restart_pid = status.get("supervisor_pid")
    if isinstance(restart_pid, int) and restart_pid > 0:
        return runtime.pid_alive(restart_pid)
    return False


def _config() -> V2Config:
    runtime.ensure_dirs()
    return runtime.ensure_config()


def main() -> int:
    stopping = False

    def request_stop(signum, frame):  # noqa: ANN001
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    config = _config()
    service_pid = runtime.start_service(wait_for_ready=False)
    bind_host = runtime.effective_ui_bind_host(config)
    ui_pid = runtime.start_ui(bind_host, config.ui.setup_port)

    if runtime.service_pid_recorded(service_pid):
        runtime.write_status("running", "incus regression started", service_pid, ui_pid)
    elif runtime.wait_for_service_pid(service_pid, timeout=runtime.SERVICE_SLOW_START_TIMEOUT_SECONDS):
        runtime.write_status("running", "incus regression started", service_pid, ui_pid)
    else:
        runtime.write_status("error", "service did not become ready", service_pid, ui_pid)
        runtime.stop_ui()
        runtime.stop_service()
        return 1

    while not stopping:
        restart_in_progress = _restart_in_progress()

        current_service_pid = _read_pid_file(paths.get_runtime_pid_path())
        if current_service_pid and current_service_pid != service_pid:
            _reap_child(service_pid)
            service_pid = current_service_pid

        current_ui_pid = _read_pid_file(paths.get_runtime_ui_pid_path()) or ui_pid
        if not current_ui_pid or not runtime.pid_alive(current_ui_pid):
            _reap_child(current_ui_pid)
            if not restart_in_progress:
                config = _config()
                ui_pid = runtime.start_ui(runtime.effective_ui_bind_host(config), config.ui.setup_port)
                runtime.write_status("running", "ui restarted in incus regression", service_pid, ui_pid)
        elif current_ui_pid != ui_pid:
            ui_pid = current_ui_pid

        if not runtime.pid_alive(service_pid):
            _reap_child(service_pid)
            current_service_pid = _read_pid_file(paths.get_runtime_pid_path())
            if current_service_pid and current_service_pid != service_pid:
                service_pid = current_service_pid
                time.sleep(1)
                continue
            if restart_in_progress:
                runtime.write_status("restarting", "incus regression restart in progress", service_pid, ui_pid)
                time.sleep(1)
                continue
            runtime.write_status("error", "service exited in incus regression", service_pid, ui_pid)
            runtime.stop_ui()
            return 1
        time.sleep(1)

    runtime.stop_ui()
    runtime.stop_service()
    runtime.write_status("stopped", "incus regression stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
