from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from config import paths
from vibe import runtime
from vibe.upgrade import get_restart_command, get_restart_environment, get_restart_invocation_command, get_safe_cwd


logger = logging.getLogger(__name__)
_RESTART_LOG_RETENTION = 10


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _restart_log_path(job_id: str) -> Path:
    paths.get_logs_dir().mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return paths.get_logs_dir() / f"restart-{timestamp}-{job_id}.log"


def _prune_restart_logs(limit: int = _RESTART_LOG_RETENTION) -> None:
    try:
        logs = sorted(
            paths.get_logs_dir().glob("restart-*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        logger.debug("Failed to list restart audit logs", exc_info=True)
        return
    for path in logs[limit:]:
        try:
            path.unlink()
        except OSError:
            logger.debug("Failed to prune restart audit log %s", path, exc_info=True)


def _write_status(payload: dict) -> None:
    status = {**payload, "updated_at": _now_iso()}
    path = runtime.get_restart_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_json(path, status)


def _read_recorded_pid() -> int | None:
    pid_path = paths.get_runtime_pid_path()
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def _read_starting_service_status() -> dict | None:
    status = runtime.read_status()
    if status.get("state") != "starting":
        return None
    return status


def _read_starting_service_pid() -> int | None:
    status = _read_starting_service_status()
    return _service_pid_from_status(status)


def _service_pid_from_status(status: dict | None) -> int | None:
    if status is None:
        return None
    pid = status.get("service_pid")
    return pid if isinstance(pid, int) and pid > 0 else None


def _fail(payload: dict, error: str, log, return_code: int) -> int:
    payload.update(ok=False, state="failed", error=error)
    _write_status(payload)
    log.write(f"{_now_iso()} {error}\n")
    log.flush()
    return return_code


def _run_restart_job(
    *,
    job_id: str,
    delay_seconds: float,
    vibe_path: str | None,
    trigger: str,
    prepare_show_runtime: bool = False,
) -> int:
    log_path = _restart_log_path(job_id)
    safe_cwd = get_safe_cwd()
    _prune_restart_logs()

    with log_path.open("a", encoding="utf-8") as log:
        def write(message: str) -> None:
            log.write(f"{_now_iso()} {message}\n")
            log.flush()

        old_pid = _read_recorded_pid()
        payload = {
            "ok": None,
            "job_id": job_id,
            "state": "scheduled" if delay_seconds > 0 else "running",
            "trigger": trigger,
            "delay_seconds": delay_seconds,
            "old_pid": old_pid,
            "new_pid": None,
            "log_path": str(log_path),
            "error": None,
            "created_at": _now_iso(),
        }
        _write_status(payload)
        write(f"restart job scheduled trigger={trigger!r} delay_seconds={delay_seconds!r} old_pid={old_pid!r}")

        if delay_seconds > 0:
            time.sleep(delay_seconds)
            payload["state"] = "running"
            _write_status(payload)
            write("restart job started after delay")

        write("stopping UI")
        ui_stopped = runtime.stop_ui()
        ui_pid = None
        try:
            ui_pid = int(paths.get_runtime_ui_pid_path().read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
        if ui_pid and ui_stopped is False and runtime.pid_alive(ui_pid):
            return _fail(payload, f"UI pid {ui_pid} did not stop", log, 2)

        write("stopping service")
        stopped = runtime.stop_service()
        if old_pid and stopped is False and runtime.pid_alive(old_pid):
            return _fail(payload, f"service pid {old_pid} did not stop", log, 2)

        write("starting service")
        command = get_restart_invocation_command(vibe_path=vibe_path)
        env = get_restart_environment(vibe_path=vibe_path)
        start_command = [*command[:-1], "start"] if command and command[-1] == "restart" else [*(command or ["vibe"]), "start"]
        try:
            result = subprocess.run(
                start_command,
                cwd=safe_cwd,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=runtime.SERVICE_SLOW_START_TIMEOUT_SECONDS + 30,
            )
        except subprocess.TimeoutExpired:
            return _fail(
                payload,
                f"start command timed out after {runtime.SERVICE_SLOW_START_TIMEOUT_SECONDS + 30:.0f} seconds",
                log,
                4,
            )
        except Exception as exc:
            return _fail(payload, f"start command failed: {exc}", log, 1)
        if result.returncode != 0:
            return _fail(payload, f"start command failed with exit code {result.returncode}", log, result.returncode or 1)

        new_pid = _read_recorded_pid()
        if not new_pid:
            starting_status = _read_starting_service_status()
            starting_pid = _service_pid_from_status(starting_status)
            if starting_pid and runtime.pid_alive(starting_pid):
                write(f"start command returned while service pid={starting_pid} is still acquiring its lock")
                if runtime.wait_for_service_pid(starting_pid, timeout=runtime.SERVICE_SLOW_START_TIMEOUT_SECONDS):
                    new_pid = starting_pid
                    ui_pid = starting_status.get("ui_pid") if starting_status else None
                    runtime.write_status("running", f"pid={new_pid}", new_pid, ui_pid if isinstance(ui_pid, int) else None)
        if not new_pid or not runtime.pid_alive(new_pid):
            return _fail(payload, "start command completed but service pid is not alive", log, 3)

        payload.update(ok=True, state="succeeded", new_pid=new_pid, error=None)
        _write_status(payload)
        write(f"restart job succeeded new_pid={new_pid}")

        if prepare_show_runtime:
            prepare_command = [
                *get_restart_command(vibe_path=vibe_path),
                "runtime",
                "prepare",
                "--strict",
            ]
            write("preparing Show Runtime after service restart")
            try:
                prepare_result = subprocess.run(
                    prepare_command,
                    cwd=safe_cwd,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                    timeout=300,
                )
                if prepare_result.returncode != 0:
                    write(f"Show Runtime preparation failed with exit code {prepare_result.returncode}")
                else:
                    write("Show Runtime preparation succeeded")
            except subprocess.TimeoutExpired:
                write("Show Runtime preparation timed out after 300 seconds")
            except Exception as exc:
                write(f"Show Runtime preparation skipped: {exc}")

        return 0


def schedule_restart(
    *,
    delay_seconds: float = 0.0,
    vibe_path: str | None = None,
    trigger: str = "cli",
    prepare_show_runtime: bool = False,
) -> dict:
    job_id = uuid.uuid4().hex[:12]
    invocation = get_restart_invocation_command(vibe_path=vibe_path)
    command = [*invocation[:-1], "__restart-supervisor"] if invocation and invocation[-1] == "restart" else [
        *(invocation or ["vibe"]),
        "__restart-supervisor",
    ]
    command.extend(["--job-id", job_id, "--delay-seconds", str(delay_seconds), "--trigger", trigger])
    if vibe_path:
        command.extend(["--vibe-path", vibe_path])
    if prepare_show_runtime:
        command.append("--prepare-show-runtime")
    env = get_restart_environment(vibe_path=vibe_path)
    log_path = _restart_log_path(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"{_now_iso()} spawning restart supervisor job_id={job_id} delay_seconds={delay_seconds!r}\n")
        log.flush()
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            cwd=get_safe_cwd(),
            env=env,
        )
    payload = {
        "ok": None,
        "job_id": job_id,
        "state": "scheduled",
        "trigger": trigger,
        "delay_seconds": delay_seconds,
        "supervisor_pid": process.pid,
        "old_pid": _read_recorded_pid(),
        "new_pid": None,
        "log_path": str(log_path),
        "error": None,
        "created_at": _now_iso(),
    }
    _write_status(payload)
    _prune_restart_logs()
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--delay-seconds", type=float, default=0.0)
    parser.add_argument("--trigger", default="cli")
    parser.add_argument("--vibe-path")
    parser.add_argument("--prepare-show-runtime", action="store_true")
    args = parser.parse_args(argv)
    return _run_restart_job(
        job_id=args.job_id,
        delay_seconds=max(0.0, args.delay_seconds),
        vibe_path=args.vibe_path,
        trigger=args.trigger,
        prepare_show_runtime=args.prepare_show_runtime,
    )


if __name__ == "__main__":
    raise SystemExit(main())
