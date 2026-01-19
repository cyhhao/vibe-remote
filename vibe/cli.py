import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from config import paths
from config.v2_config import (
    AgentsConfig,
    ClaudeConfig,
    CodexConfig,
    OpenCodeConfig,
    RuntimeConfig,
    SlackConfig,
    V2Config,
)
from vibe import runtime


def _write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _open_browser(url):
    try:
        subprocess.Popen(["open", url])
    except Exception:
        pass


def _default_config():
    return V2Config(
        mode="self_host",
        version="v2",
        slack=SlackConfig(bot_token="", app_token=""),
        runtime=RuntimeConfig(default_cwd=str(Path.cwd())),
        agents=AgentsConfig(
            default_backend="opencode",
            opencode=OpenCodeConfig(enabled=True, cli_path="opencode"),
            claude=ClaudeConfig(enabled=True, cli_path="claude"),
            codex=CodexConfig(enabled=False, cli_path="codex"),
        ),
    )


def _ensure_config():
    config_path = paths.get_config_path()
    if not config_path.exists():
        default = _default_config()
        default.save(config_path)
    return V2Config.load(config_path)


def _write_status(state, detail=None):
    payload = {
        "state": state,
        "detail": detail,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _write_json(paths.get_runtime_status_path(), payload)


def _spawn_background(
    args,
    pid_path,
    stdout_name: str = "service_stdout.log",
    stderr_name: str = "service_stderr.log",
):
    stdout_path = paths.get_runtime_dir() / stdout_name
    stderr_path = paths.get_runtime_dir() / stderr_name
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout = stdout_path.open("ab")
    stderr = stderr_path.open("ab")
    process = subprocess.Popen(
        args,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    stdout.close()
    stderr.close()
    pid_path.write_text(str(process.pid), encoding="utf-8")
    return process.pid


def _stop_process(pid_path):
    if not pid_path.exists():
        return False
    pid = int(pid_path.read_text(encoding="utf-8").strip())
    if not _pid_alive(pid):
        pid_path.unlink(missing_ok=True)
        return False
    os.kill(pid, signal.SIGTERM)
    pid_path.unlink(missing_ok=True)
    return True


def _render_status():
    status = _read_json(paths.get_runtime_status_path()) or {}
    pid_path = paths.get_runtime_pid_path()
    pid = pid_path.read_text(encoding="utf-8").strip() if pid_path.exists() else None
    running = bool(pid and pid.isdigit() and _pid_alive(int(pid)))
    status["running"] = running
    status["pid"] = int(pid) if pid and pid.isdigit() else None
    return json.dumps(status, indent=2)


def _doctor():
    checks = {}
    config_path = paths.get_config_path()
    if config_path.exists():
        checks["config"] = {
            "status": "ok",
            "detail": str(config_path),
        }
    else:
        checks["config"] = {"status": "error", "detail": "config.json not found"}

    config = None
    try:
        config = V2Config.load(config_path)
        checks["config_load"] = {"status": "ok", "detail": "loaded"}
    except Exception as exc:
        checks["config_load"] = {"status": "error", "detail": str(exc)}

    if config:
        try:
            config.slack.validate()
            checks["slack"] = {"status": "ok", "detail": "token format ok"}
        except Exception as exc:
            checks["slack"] = {"status": "error", "detail": str(exc)}

        checks["opencode"] = {
            "status": "ok" if config.agents.opencode.enabled else "skip",
            "detail": config.agents.opencode.cli_path,
        }
        checks["claude"] = {
            "status": "ok" if config.agents.claude.enabled else "skip",
            "detail": config.agents.claude.cli_path,
        }
        checks["codex"] = {
            "status": "ok" if config.agents.codex.enabled else "skip",
            "detail": config.agents.codex.cli_path,
        }

    ok = all(check["status"] in ("ok", "skip") for check in checks.values())
    result = {"ok": ok, "checks": checks}
    _write_json(paths.get_runtime_doctor_path(), result)
    return result


def _run_background_service():
    python = sys.executable
    command = "import runpy; runpy.run_path('main.py', run_name='__main__')"
    return _spawn_background(
        [python, "-c", command],
        paths.get_runtime_pid_path(),
        "service_stdout.log",
        "service_stderr.log",
    )


def cmd_vibe():
    paths.ensure_data_dirs()
    config = _ensure_config()

    # Always restart both processes
    runtime.stop_service()
    runtime.stop_ui()

    if not config.slack.bot_token:
        _write_status("setup", "missing Slack bot token")
    else:
        _write_status("starting")

    service_pid = _run_background_service()
    ui_pid = runtime.start_ui(config.ui.setup_host, config.ui.setup_port)
    runtime.write_status("running", "pid={}".format(service_pid), service_pid, ui_pid)

    ui_url = "http://{}:{}".format(config.ui.setup_host, config.ui.setup_port)
    if config.ui.open_browser:
        _open_browser(ui_url)

    return 0



def cmd_stop():
    runtime.stop_service()
    runtime.stop_ui()
    _write_status("stopped")
    return 0


def cmd_status():
    print(_render_status())
    return 0


def cmd_doctor():
    result = _doctor()
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


def build_parser():
    parser = argparse.ArgumentParser(prog="vibe")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("stop")
    subparsers.add_parser("status")
    subparsers.add_parser("doctor")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "stop":
        sys.exit(cmd_stop())
    if args.command == "status":
        sys.exit(cmd_status())
    if args.command == "doctor":
        sys.exit(cmd_doctor())
    sys.exit(cmd_vibe())
