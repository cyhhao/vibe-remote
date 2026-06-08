#!/usr/bin/env python3
"""Run Avibe service + Web UI inside the Incus regression systemd service."""

from __future__ import annotations

import signal
import sys
import time

from config import paths
from config.v2_config import V2Config
from vibe import runtime


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
        current_ui_pid = ui_pid
        ui_pid_path = paths.get_runtime_ui_pid_path()
        if ui_pid_path.exists():
            try:
                current_ui_pid = int(ui_pid_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                current_ui_pid = 0
        if not current_ui_pid or not runtime.pid_alive(current_ui_pid):
            config = _config()
            ui_pid = runtime.start_ui(runtime.effective_ui_bind_host(config), config.ui.setup_port)
            runtime.write_status("running", "ui restarted in incus regression", service_pid, ui_pid)
        if not runtime.pid_alive(service_pid):
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
