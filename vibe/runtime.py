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


ROOT_DIR = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT_DIR / "main.py"


def ensure_dirs():
    paths.ensure_data_dirs()


def default_config():
    return V2Config(
        mode="self_host",
        version="v2",
        slack=SlackConfig(bot_token="", app_token=""),
        runtime=RuntimeConfig(default_cwd=str(ROOT_DIR / "_tmp")),
        agents=AgentsConfig(
            default_backend="opencode",
            opencode=OpenCodeConfig(enabled=True, cli_path="opencode"),
            claude=ClaudeConfig(enabled=True, cli_path="claude"),
            codex=CodexConfig(enabled=False, cli_path="codex"),
        ),
    )


def ensure_config():
    config_path = paths.get_config_path()
    if not config_path.exists():
        default = default_config()
        default.save(config_path)
    return V2Config.load(config_path)


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def spawn_background(args, pid_path):
    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(ROOT_DIR),
    )
    pid_path.write_text(str(process.pid), encoding="utf-8")
    return process.pid


def stop_process(pid_path):
    if not pid_path.exists():
        return False
    pid = int(pid_path.read_text(encoding="utf-8").strip())
    if not pid_alive(pid):
        pid_path.unlink(missing_ok=True)
        return False
    os.kill(pid, signal.SIGTERM)
    pid_path.unlink(missing_ok=True)
    return True


def write_status(state, detail=None, service_pid=None, ui_pid=None):
    payload = {
        "state": state,
        "detail": detail,
        "service_pid": service_pid,
        "ui_pid": ui_pid,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_json(paths.get_runtime_status_path(), payload)


def read_status():
    return read_json(paths.get_runtime_status_path()) or {}


def render_status():
    status = read_status()
    pid_path = paths.get_runtime_pid_path()
    pid = pid_path.read_text(encoding="utf-8").strip() if pid_path.exists() else None
    running = bool(pid and pid.isdigit() and pid_alive(int(pid)))
    status["running"] = running
    status["pid"] = int(pid) if pid and pid.isdigit() else None
    return json.dumps(status, indent=2)


def start_service():
    return spawn_background(
        [sys.executable, str(MAIN_PATH)], paths.get_runtime_pid_path()
    )


def start_ui(port):
    return spawn_background(
        [sys.executable, "-m", "vibe", "ui", "--port", str(port)],
        paths.get_runtime_ui_pid_path(),
    )


def stop_service():
    return stop_process(paths.get_runtime_pid_path())


def stop_ui():
    return stop_process(paths.get_runtime_ui_pid_path())
