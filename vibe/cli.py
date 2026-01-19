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
    """Run diagnostic checks and return results in UI-compatible format.
    
    Returns:
        {
            "groups": [{"name": "...", "items": [{"status": "pass|warn|fail", "message": "...", "action": "..."}]}],
            "summary": {"pass": 0, "warn": 0, "fail": 0},
            "ok": bool
        }
    """
    groups = []
    summary = {"pass": 0, "warn": 0, "fail": 0}
    
    # Configuration Group
    config_items = []
    config_path = paths.get_config_path()
    
    if config_path.exists():
        config_items.append({
            "status": "pass",
            "message": f"Configuration file found: {config_path}",
        })
        summary["pass"] += 1
    else:
        config_items.append({
            "status": "fail",
            "message": "Configuration file not found",
            "action": "Run 'vibe' to create initial configuration",
        })
        summary["fail"] += 1
    
    config = None
    try:
        config = V2Config.load(config_path)
        config_items.append({
            "status": "pass",
            "message": "Configuration loaded successfully",
        })
        summary["pass"] += 1
    except Exception as exc:
        config_items.append({
            "status": "fail",
            "message": f"Failed to load configuration: {exc}",
            "action": "Check config.json syntax or delete and reconfigure",
        })
        summary["fail"] += 1
    
    groups.append({"name": "Configuration", "items": config_items})
    
    # Slack Group
    slack_items = []
    if config:
        try:
            config.slack.validate()
            slack_items.append({
                "status": "pass",
                "message": "Slack token format is valid",
            })
            summary["pass"] += 1
            
            # Check if tokens are actually set
            if config.slack.bot_token:
                slack_items.append({
                    "status": "pass",
                    "message": "Bot token is configured",
                })
                summary["pass"] += 1
            else:
                slack_items.append({
                    "status": "warn",
                    "message": "Bot token is not configured",
                    "action": "Add your Slack bot token in the setup wizard",
                })
                summary["warn"] += 1
                
            if config.slack.app_token:
                slack_items.append({
                    "status": "pass",
                    "message": "App token is configured (Socket Mode)",
                })
                summary["pass"] += 1
            else:
                slack_items.append({
                    "status": "warn",
                    "message": "App token is not configured",
                    "action": "Add your Slack app token for Socket Mode",
                })
                summary["warn"] += 1
                
        except Exception as exc:
            slack_items.append({
                "status": "fail",
                "message": f"Slack token validation failed: {exc}",
                "action": "Check your Slack tokens in the setup wizard",
            })
            summary["fail"] += 1
    else:
        slack_items.append({
            "status": "fail",
            "message": "Cannot check Slack: configuration not loaded",
        })
        summary["fail"] += 1
    
    groups.append({"name": "Slack", "items": slack_items})
    
    # Agent Backends Group
    agent_items = []
    if config:
        # OpenCode
        if config.agents.opencode.enabled:
            cli_path = config.agents.opencode.cli_path
            import shutil
            found_path = shutil.which(cli_path) if cli_path else None
            if found_path:
                agent_items.append({
                    "status": "pass",
                    "message": f"OpenCode CLI found: {found_path}",
                })
                summary["pass"] += 1
            else:
                agent_items.append({
                    "status": "warn",
                    "message": f"OpenCode CLI not found: {cli_path}",
                    "action": "Install OpenCode or update CLI path",
                })
                summary["warn"] += 1
        else:
            agent_items.append({
                "status": "pass",
                "message": "OpenCode: disabled",
            })
            summary["pass"] += 1
        
        # Claude
        if config.agents.claude.enabled:
            cli_path = config.agents.claude.cli_path
            import shutil
            # Check preferred location first
            preferred = Path.home() / ".claude" / "local" / "claude"
            if preferred.exists() and os.access(preferred, os.X_OK):
                found_path = str(preferred)
            else:
                found_path = shutil.which(cli_path) if cli_path else None
            
            if found_path:
                agent_items.append({
                    "status": "pass",
                    "message": f"Claude CLI found: {found_path}",
                })
                summary["pass"] += 1
            else:
                agent_items.append({
                    "status": "warn",
                    "message": f"Claude CLI not found: {cli_path}",
                    "action": "Install Claude Code or update CLI path",
                })
                summary["warn"] += 1
        else:
            agent_items.append({
                "status": "pass",
                "message": "Claude: disabled",
            })
            summary["pass"] += 1
        
        # Codex
        if config.agents.codex.enabled:
            cli_path = config.agents.codex.cli_path
            import shutil
            found_path = shutil.which(cli_path) if cli_path else None
            if found_path:
                agent_items.append({
                    "status": "pass",
                    "message": f"Codex CLI found: {found_path}",
                })
                summary["pass"] += 1
            else:
                agent_items.append({
                    "status": "warn",
                    "message": f"Codex CLI not found: {cli_path}",
                    "action": "Install Codex or update CLI path",
                })
                summary["warn"] += 1
        else:
            agent_items.append({
                "status": "pass",
                "message": "Codex: disabled",
            })
            summary["pass"] += 1
        
        # Default backend check
        default_backend = config.agents.default_backend
        agent_items.append({
            "status": "pass",
            "message": f"Default backend: {default_backend}",
        })
        summary["pass"] += 1
    else:
        agent_items.append({
            "status": "fail",
            "message": "Cannot check agents: configuration not loaded",
        })
        summary["fail"] += 1
    
    groups.append({"name": "Agent Backends", "items": agent_items})
    
    # Runtime Group
    runtime_items = []
    if config:
        cwd = config.runtime.default_cwd
        if cwd and os.path.isdir(cwd):
            runtime_items.append({
                "status": "pass",
                "message": f"Working directory: {cwd}",
            })
            summary["pass"] += 1
        else:
            runtime_items.append({
                "status": "warn",
                "message": f"Working directory does not exist: {cwd}",
                "action": "Update default_cwd in settings",
            })
            summary["warn"] += 1
        
        runtime_items.append({
            "status": "pass",
            "message": f"Log level: {config.runtime.log_level}",
        })
        summary["pass"] += 1
    
    # Check log file
    log_path = paths.get_logs_dir() / "vibe_remote.log"
    if log_path.exists():
        runtime_items.append({
            "status": "pass",
            "message": f"Log file: {log_path}",
        })
        summary["pass"] += 1
    else:
        runtime_items.append({
            "status": "pass",
            "message": "Log file will be created on first run",
        })
        summary["pass"] += 1
    
    groups.append({"name": "Runtime", "items": runtime_items})
    
    # Calculate overall status
    ok = summary["fail"] == 0
    
    result = {
        "groups": groups,
        "summary": summary,
        "ok": ok,
    }
    
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
    
    # Terminal-friendly output
    print("\n  Vibe Remote Diagnostics")
    print("  " + "=" * 40)
    
    for group in result.get("groups", []):
        print(f"\n  {group['name']}")
        print("  " + "-" * 30)
        for item in group.get("items", []):
            status = item["status"]
            if status == "pass":
                icon = "\033[32m✓\033[0m"  # Green checkmark
            elif status == "warn":
                icon = "\033[33m!\033[0m"  # Yellow warning
            else:
                icon = "\033[31m✗\033[0m"  # Red X
            
            print(f"  {icon} {item['message']}")
            if item.get("action"):
                print(f"      → {item['action']}")
    
    summary = result.get("summary", {})
    print("\n  " + "-" * 30)
    print(f"  \033[32m{summary.get('pass', 0)} passed\033[0m  "
          f"\033[33m{summary.get('warn', 0)} warnings\033[0m  "
          f"\033[31m{summary.get('fail', 0)} failed\033[0m")
    print()
    
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
