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


def get_package_root() -> Path:
    """Get the root directory of the vibe package."""
    return Path(__file__).resolve().parent


def get_project_root() -> Path:
    """Get the project root directory (for development mode)."""
    return Path(__file__).resolve().parents[1]


def get_ui_dist_path() -> Path:
    """Get the path to UI dist directory."""
    # First check if we're in development mode (ui/dist exists at project root)
    project_root = get_project_root()
    dev_ui_path = project_root / "ui" / "dist"
    if dev_ui_path.exists():
        return dev_ui_path
    
    # Then check if UI is bundled with the package
    package_ui_path = get_package_root() / "ui" / "dist"
    if package_ui_path.exists():
        return package_ui_path
    
    # Fallback to development path
    return dev_ui_path


def get_service_main_path() -> Path:
    """Get the path to the main service entry point."""
    # First check if we're in development mode (main.py exists at project root)
    project_root = get_project_root()
    dev_main_path = project_root / "main.py"
    if dev_main_path.exists():
        return dev_main_path
    
    # Then check if service_main.py is bundled with the package
    package_main_path = get_package_root() / "service_main.py"
    if package_main_path.exists():
        return package_main_path
    
    # Fallback to development path
    return dev_main_path


def get_working_dir() -> Path:
    """Get the working directory for subprocess execution."""
    # In development mode, use project root
    project_root = get_project_root()
    if (project_root / "main.py").exists():
        return project_root
    
    # In installed mode, use package root
    return get_package_root()


ROOT_DIR = get_project_root()  # For backward compatibility
MAIN_PATH = get_service_main_path()


def ensure_dirs():
    paths.ensure_data_dirs()


def default_config():
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


def _log_path(name: str) -> Path:
    return paths.get_runtime_dir() / name


def spawn_background(args, pid_path, stdout_name: str, stderr_name: str):
    stdout_path = _log_path(stdout_name)
    stderr_path = _log_path(stderr_name)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout = stdout_path.open("ab")
    stderr = stderr_path.open("ab")
    process = subprocess.Popen(
        args,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
        cwd=str(get_working_dir()),
        close_fds=True,
    )
    stdout.close()
    stderr.close()
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
    main_path = get_service_main_path()
    return spawn_background(
        [sys.executable, str(main_path)],
        paths.get_runtime_pid_path(),
        "service_stdout.log",
        "service_stderr.log",
    )


def start_ui(host, port):
    command = "from vibe.ui_server import run_ui_server; run_ui_server('{}', {})".format(
        host, port
    )
    return spawn_background(
        [sys.executable, "-c", command],
        paths.get_runtime_ui_pid_path(),
        "ui_stdout.log",
        "ui_stderr.log",
    )


def stop_service():
    return stop_process(paths.get_runtime_pid_path())


def stop_ui():
    return stop_process(paths.get_runtime_ui_pid_path())
