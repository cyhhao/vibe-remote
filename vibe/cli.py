import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from tzlocal import get_localzone_name

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
from core.scheduled_tasks import ScheduledTaskStore, parse_session_key
from vibe import __version__, api, runtime
from vibe.upgrade import build_upgrade_plan, cache_running_vibe_path, get_latest_version_info, get_safe_cwd

logger = logging.getLogger(__name__)


class VibeArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        self.error_help_command = kwargs.pop("error_help_command", None)
        self.error_hint = kwargs.pop("error_hint", None)
        super().__init__(*args, **kwargs)

    def error(self, message):
        payload = {
            "ok": False,
            "code": "invalid_arguments",
            "error": message,
            "usage": self.format_usage().strip(),
        }
        if self.error_hint:
            payload["hint"] = self.error_hint
        if self.error_help_command:
            payload["help_command"] = self.error_help_command
        self.exit(2, json.dumps(payload, indent=2) + "\n")


class TaskCliError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        hint: str | None = None,
        example: str | None = None,
        help_command: str | None = None,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.example = example
        self.help_command = help_command
        self.details = details or {}


def _print_task_error(exc: Exception, *, help_command: str | None = None) -> None:
    if isinstance(exc, TaskCliError):
        payload = {
            "ok": False,
            "code": exc.code,
            "error": str(exc),
        }
        if exc.hint:
            payload["hint"] = exc.hint
        if exc.example:
            payload["example"] = exc.example
        if exc.help_command or help_command:
            payload["help_command"] = exc.help_command or help_command
        if exc.details:
            payload["details"] = exc.details
    else:
        payload = {
            "ok": False,
            "code": "task_command_failed",
            "error": str(exc),
        }
        if help_command:
            payload["help_command"] = help_command
    print(json.dumps(payload, indent=2), file=sys.stderr)


def _task_examples_text() -> str:
    return dedent(
        """\
        Examples:
          vibe task add --session-key 'slack::channel::C123' --cron '0 * * * *' --prompt 'Share the hourly summary.'
          vibe task add --session-key 'discord::user::123456789' --at '2026-03-31T09:00:00+08:00' --prompt-file briefing.md
          vibe task add --session-key 'lark::channel::oc_abc::thread::om_123' --cron '30 9 * * 1-5' --prompt 'Post the daily standup reminder in this thread.'
        """
    )


def _task_add_examples_text() -> str:
    return dedent(
        """\
        Session key format:
          <platform>::channel::<channel_id>
          <platform>::user::<user_id>
          <platform>::channel::<channel_id>::thread::<thread_id>
          <platform>::user::<user_id>::thread::<thread_id>

        Guidance:
          Prefer a threadless session key by default.
          Only append ::thread::<thread_id> when the task must continue inside a specific thread.
          Use --cron for recurring jobs and --at for one-shot jobs.
          --timezone controls how --cron and naive --at timestamps are interpreted.

        Examples:
          vibe task add --session-key 'slack::channel::C123' --cron '0 * * * *' --prompt 'Share the hourly summary.'
          vibe task add --session-key 'discord::user::123456789' --at '2026-03-31T09:00:00+08:00' --prompt 'Send the release reminder.'
          vibe task add --session-key 'lark::channel::oc_abc::thread::om_123' --cron '30 9 * * 1-5' --timezone 'Asia/Shanghai' --prompt-file standup.txt
        """
    )


def _add_hidden_task_alias(task_subparsers, alias: str, parser) -> None:
    alias_parser = task_subparsers.add_parser(
        alias,
        help=argparse.SUPPRESS,
        parents=[parser],
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    alias_parser.error_help_command = getattr(parser, "error_help_command", None)
    alias_parser.error_hint = getattr(parser, "error_hint", None)
    task_subparsers._choices_actions = [  # type: ignore[attr-defined]
        action for action in task_subparsers._choices_actions if action.dest != alias  # type: ignore[attr-defined]
    ]


def _write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _pid_alive(pid):
    return runtime.pid_alive(pid)


def _in_ssh_session() -> bool:
    """Best-effort detection for SSH sessions."""
    return any(os.environ.get(key) for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"))


def _open_browser(url: str) -> bool:
    """Open a URL in the default browser (best effort).

    Returns True if a launch attempt was made successfully.
    """
    try:
        import webbrowser

        if webbrowser.open(url):
            return True
    except Exception:
        pass

    # Fallbacks for environments where webbrowser isn't configured.
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url])
            return True
        if os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "", url])
            return True
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", url])
            return True
    except Exception:
        pass

    return False


def _default_config():
    return V2Config(
        mode="self_host",
        version="v2",
        slack=SlackConfig(bot_token="", app_token=""),
        runtime=RuntimeConfig(default_cwd=str(Path.home() / "work")),
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
    return runtime.stop_process(pid_path)


def _render_status():
    status = _read_json(paths.get_runtime_status_path()) or {}
    pid_path = paths.get_runtime_pid_path()
    pid = pid_path.read_text(encoding="utf-8").strip() if pid_path.exists() else None
    running = bool(pid and pid.isdigit() and _pid_alive(int(pid)))
    status["running"] = running
    status["pid"] = int(pid) if pid and pid.isdigit() else None
    return json.dumps(status, indent=2)


def _default_timezone_name() -> str:
    try:
        return get_localzone_name()
    except Exception:
        tz = datetime.now().astimezone().tzinfo
        key = getattr(tz, "key", None)
        if key:
            return str(key)
    return "UTC"


def _resolve_task_prompt(args) -> str:
    prompt = (getattr(args, "prompt", None) or "").strip()
    prompt_file = getattr(args, "prompt_file", None)
    if prompt and prompt_file:
        raise TaskCliError(
            "use either --prompt or --prompt-file",
            code="conflicting_prompt_inputs",
            hint="Pass inline text with --prompt or load it from disk with --prompt-file, but not both.",
            help_command="vibe task add --help",
        )
    if prompt:
        return prompt
    if getattr(args, "prompt", None) is not None:
        raise TaskCliError(
            "prompt text cannot be empty",
            code="empty_prompt",
            hint="Provide non-empty text after --prompt, or use --prompt-file with a readable text file.",
            help_command="vibe task add --help",
        )
    if prompt_file:
        try:
            content = Path(prompt_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise TaskCliError(
                f"failed to read prompt file: {exc}",
                code="prompt_file_read_failed",
                hint="Use --prompt-file with a readable UTF-8 text file.",
                example="vibe task add --session-key 'slack::channel::C123' --cron '0 * * * *' --prompt-file briefing.md",
                help_command="vibe task add --help",
                details={"prompt_file": prompt_file},
            ) from exc
        if not content:
            raise TaskCliError(
                "prompt file is empty",
                code="empty_prompt",
                hint="Put the prompt text in the file, or pass it directly with --prompt.",
                example="vibe task add --session-key 'slack::channel::C123' --cron '0 * * * *' --prompt 'Share the hourly summary.'",
                help_command="vibe task add --help",
                details={"prompt_file": prompt_file},
            )
        return content
    raise TaskCliError(
        "one of --prompt or --prompt-file is required",
        code="missing_prompt",
        hint="Pass inline text with --prompt or load it from disk with --prompt-file.",
        help_command="vibe task add --help",
    )


def _normalize_run_at(value: str, timezone_name: str) -> str:
    dt = datetime.fromisoformat(value)
    tz = ZoneInfo(timezone_name)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt.isoformat()


def _task_payload(task):
    return task.to_dict()


def _task_store() -> ScheduledTaskStore:
    return ScheduledTaskStore()


def _supported_task_platforms() -> set[str]:
    try:
        config = _ensure_config()
    except Exception:
        return set()
    enabled = getattr(config, "enabled_platforms", None)
    if callable(enabled):
        return set(enabled())
    return {getattr(config, "platform", "slack")}


def cmd_task_add(args):
    try:
        try:
            parsed = parse_session_key(args.session_key)
        except ValueError as exc:
            raise TaskCliError(
                str(exc),
                code="invalid_session_key",
                hint="Use <platform>::<channel|user>::<id>[::thread::<thread_id>]. Prefer a threadless key unless the task must reply in one specific thread.",
                example="slack::channel::C123",
                help_command="vibe task add --help",
                details={"session_key": args.session_key},
            ) from exc

        supported_platforms = _supported_task_platforms()
        if parsed.platform not in supported_platforms:
            supported_text = ", ".join(sorted(supported_platforms)) or "none"
            raise TaskCliError(
                f"unsupported task platform: {parsed.platform}",
                code="unsupported_platform",
                hint="Choose a platform that is enabled in Vibe Remote before creating the task.",
                example="slack::channel::C123",
                help_command="vibe task add --help",
                details={
                    "requested_platform": parsed.platform,
                    "configured_platforms": sorted(supported_platforms),
                    "configured_platforms_text": supported_text,
                },
            )
        prompt = _resolve_task_prompt(args)
        timezone_name = args.timezone or _default_timezone_name()
        try:
            timezone = ZoneInfo(timezone_name)
        except Exception as exc:
            raise TaskCliError(
                f"invalid timezone: {timezone_name}",
                code="invalid_timezone",
                hint="Use a valid IANA timezone such as UTC, Asia/Shanghai, or America/Los_Angeles.",
                example="Asia/Shanghai",
                help_command="vibe task add --help",
                details={"timezone": timezone_name},
            ) from exc
        store = _task_store()

        if args.cron:
            try:
                CronTrigger.from_crontab(args.cron, timezone=timezone)
            except ValueError as exc:
                raise TaskCliError(
                    f"invalid cron expression: {args.cron}",
                    code="invalid_cron",
                    hint="Use standard 5-field crontab format: minute hour day-of-month month day-of-week.",
                    example="0 * * * *",
                    help_command="vibe task add --help",
                    details={"cron": args.cron},
                ) from exc
            task = store.add_task(
                session_key=args.session_key,
                prompt=prompt,
                schedule_type="cron",
                cron=args.cron,
                timezone_name=timezone_name,
            )
        else:
            try:
                run_at = _normalize_run_at(args.at, timezone_name)
            except ValueError as exc:
                raise TaskCliError(
                    f"invalid --at timestamp: {args.at}",
                    code="invalid_run_at",
                    hint="Use ISO 8601, for example 2026-03-31T09:00:00+08:00 or 2026-03-31T09:00:00.",
                    example="2026-03-31T09:00:00+08:00",
                    help_command="vibe task add --help",
                    details={"at": args.at, "timezone": timezone_name},
                ) from exc
            task = store.add_task(
                session_key=args.session_key,
                prompt=prompt,
                schedule_type="at",
                run_at=run_at,
                timezone_name=timezone_name,
            )
        print(json.dumps({"ok": True, "task": _task_payload(task)}, indent=2))
        return 0
    except Exception as exc:
        _print_task_error(exc, help_command="vibe task add --help")
        return 1


def cmd_task_list():
    store = _task_store()
    print(json.dumps({"tasks": [_task_payload(task) for task in store.list_tasks()]}, indent=2))
    return 0


def cmd_task_show(task_id: str):
    store = _task_store()
    task = store.get_task(task_id)
    if task is None:
        _print_task_error(
            TaskCliError(
                f"task '{task_id}' not found",
                code="task_not_found",
                hint="Use 'vibe task list' to find a valid task ID before calling show.",
                help_command="vibe task list",
                details={"task_id": task_id},
            )
        )
        return 1
    print(json.dumps({"ok": True, "task": _task_payload(task)}, indent=2))
    return 0


def cmd_task_set_enabled(task_id: str, enabled: bool):
    store = _task_store()
    task = store.get_task(task_id)
    if task is None:
        action = "resume" if enabled else "pause"
        _print_task_error(
            TaskCliError(
                f"task '{task_id}' not found",
                code="task_not_found",
                hint=f"Use 'vibe task list' to find a valid task ID before calling {action}.",
                help_command="vibe task list",
                details={"task_id": task_id},
            )
        )
        return 1
    updated = store.set_enabled(task_id, enabled)
    print(json.dumps({"ok": True, "task": _task_payload(updated)}, indent=2))
    return 0


def cmd_task_remove(task_id: str):
    store = _task_store()
    removed = store.remove_task(task_id)
    if not removed:
        _print_task_error(
            TaskCliError(
                f"task '{task_id}' not found",
                code="task_not_found",
                hint="Use 'vibe task list' to find a valid task ID before calling remove.",
                help_command="vibe task list",
                details={"task_id": task_id},
            )
        )
        return 1
    print(json.dumps({"ok": True, "removed_id": task_id}, indent=2))
    return 0


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
        config_items.append(
            {
                "status": "pass",
                "message": f"Configuration file found: {config_path}",
            }
        )
        summary["pass"] += 1
    else:
        config_items.append(
            {
                "status": "fail",
                "message": "Configuration file not found",
                "action": "Run 'vibe' to create initial configuration",
            }
        )
        summary["fail"] += 1

    config = None
    try:
        config = V2Config.load(config_path)
        config_items.append(
            {
                "status": "pass",
                "message": "Configuration loaded successfully",
            }
        )
        summary["pass"] += 1
    except Exception as exc:
        config_items.append(
            {
                "status": "fail",
                "message": f"Failed to load configuration: {exc}",
                "action": "Check config.json syntax or delete and reconfigure",
            }
        )
        summary["fail"] += 1

    groups.append({"name": "Configuration", "items": config_items})

    # Slack Group
    slack_items = []
    if config:
        try:
            config.slack.validate()
            slack_items.append(
                {
                    "status": "pass",
                    "message": "Slack token format is valid",
                }
            )
            summary["pass"] += 1

            # Check if tokens are actually set
            if config.slack.bot_token:
                slack_items.append(
                    {
                        "status": "pass",
                        "message": "Bot token is configured",
                    }
                )
                summary["pass"] += 1
            else:
                slack_items.append(
                    {
                        "status": "warn",
                        "message": "Bot token is not configured",
                        "action": "Add your Slack bot token in the setup wizard",
                    }
                )
                summary["warn"] += 1

            if config.slack.app_token:
                slack_items.append(
                    {
                        "status": "pass",
                        "message": "App token is configured (Socket Mode)",
                    }
                )
                summary["pass"] += 1
            else:
                slack_items.append(
                    {
                        "status": "warn",
                        "message": "App token is not configured",
                        "action": "Add your Slack app token for Socket Mode",
                    }
                )
                summary["warn"] += 1

        except Exception as exc:
            slack_items.append(
                {
                    "status": "fail",
                    "message": f"Slack token validation failed: {exc}",
                    "action": "Check your Slack tokens in the setup wizard",
                }
            )
            summary["fail"] += 1
    else:
        slack_items.append(
            {
                "status": "fail",
                "message": "Cannot check Slack: configuration not loaded",
            }
        )
        summary["fail"] += 1

    groups.append({"name": "Slack", "items": slack_items})

    # Agent Backends Group
    agent_items = []
    if config:
        # OpenCode
        if config.agents.opencode.enabled:
            cli_path = config.agents.opencode.cli_path
            found_path = api.detect_cli(cli_path).get("path") if cli_path else None
            if found_path:
                agent_items.append(
                    {
                        "status": "pass",
                        "message": f"OpenCode CLI found: {found_path}",
                    }
                )
                summary["pass"] += 1
            else:
                agent_items.append(
                    {
                        "status": "warn",
                        "message": f"OpenCode CLI not found: {cli_path}",
                        "action": "Install OpenCode or update CLI path",
                    }
                )
                summary["warn"] += 1
        else:
            agent_items.append(
                {
                    "status": "pass",
                    "message": "OpenCode: disabled",
                }
            )
            summary["pass"] += 1

        # Claude
        if config.agents.claude.enabled:
            cli_path = config.agents.claude.cli_path
            found_path = api.detect_cli(cli_path).get("path") if cli_path else None

            if found_path:
                agent_items.append(
                    {
                        "status": "pass",
                        "message": f"Claude CLI found: {found_path}",
                    }
                )
                summary["pass"] += 1
            else:
                agent_items.append(
                    {
                        "status": "warn",
                        "message": f"Claude CLI not found: {cli_path}",
                        "action": "Install Claude Code or update CLI path",
                    }
                )
                summary["warn"] += 1
        else:
            agent_items.append(
                {
                    "status": "pass",
                    "message": "Claude: disabled",
                }
            )
            summary["pass"] += 1

        # Codex
        if config.agents.codex.enabled:
            cli_path = config.agents.codex.cli_path
            found_path = api.detect_cli(cli_path).get("path") if cli_path else None
            if found_path:
                agent_items.append(
                    {
                        "status": "pass",
                        "message": f"Codex CLI found: {found_path}",
                    }
                )
                summary["pass"] += 1
            else:
                agent_items.append(
                    {
                        "status": "warn",
                        "message": f"Codex CLI not found: {cli_path}",
                        "action": "Install Codex or update CLI path",
                    }
                )
                summary["warn"] += 1
        else:
            agent_items.append(
                {
                    "status": "pass",
                    "message": "Codex: disabled",
                }
            )
            summary["pass"] += 1

        # Default backend check
        default_backend = config.agents.default_backend
        agent_items.append(
            {
                "status": "pass",
                "message": f"Default backend: {default_backend}",
            }
        )
        summary["pass"] += 1
    else:
        agent_items.append(
            {
                "status": "fail",
                "message": "Cannot check agents: configuration not loaded",
            }
        )
        summary["fail"] += 1

    groups.append({"name": "Agent Backends", "items": agent_items})

    # Runtime Group
    runtime_items = []
    if config:
        cwd = config.runtime.default_cwd
        if cwd and os.path.isdir(cwd):
            runtime_items.append(
                {
                    "status": "pass",
                    "message": f"Working directory: {cwd}",
                }
            )
            summary["pass"] += 1
        else:
            runtime_items.append(
                {
                    "status": "warn",
                    "message": f"Working directory does not exist: {cwd}",
                    "action": "Update default_cwd in settings",
                }
            )
            summary["warn"] += 1

        runtime_items.append(
            {
                "status": "pass",
                "message": f"Log level: {config.runtime.log_level}",
            }
        )
        summary["pass"] += 1

    # Check log file
    log_path = paths.get_logs_dir() / "vibe_remote.log"
    if log_path.exists():
        runtime_items.append(
            {
                "status": "pass",
                "message": f"Log file: {log_path}",
            }
        )
        summary["pass"] += 1
    else:
        runtime_items.append(
            {
                "status": "pass",
                "message": "Log file will be created on first run",
            }
        )
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

    service_pid = runtime.start_service()
    ui_pid = runtime.start_ui(config.ui.setup_host, config.ui.setup_port)
    runtime.write_status("running", "pid={}".format(service_pid), service_pid, ui_pid)

    ui_url = "http://{}:{}".format(config.ui.setup_host, config.ui.setup_port)

    # Always print Web UI access instructions.
    print("Web UI:")
    print(f"  {ui_url}")
    print("")
    port = int(config.ui.setup_port)
    print("If you are running Vibe Remote on a remote server, use SSH port forwarding on your local machine:")
    print(f"  ssh -NL {port}:localhost:{port} user@server-ip")
    print("")
    print("Then open in your local browser:")
    print(f"  http://127.0.0.1:{port}")
    print("")

    # If running over SSH, avoid trying to open a browser on the server.
    if config.ui.open_browser and not _in_ssh_session():
        opened = _open_browser(ui_url)
        if not opened:
            print(f"(Tip) Could not auto-open a browser. Open this URL manually: {ui_url}")
            print("")

    return 0


def _stop_opencode_server():
    """Terminate the OpenCode server if running."""
    pid_file = paths.get_logs_dir() / "opencode_server.json"
    if not pid_file.exists():
        return False

    try:
        info = json.loads(pid_file.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("Failed to parse OpenCode PID file: %s", e)
        return False

    pid = info.get("pid") if isinstance(info, dict) else None
    if not isinstance(pid, int) or not _pid_alive(pid):
        pid_file.unlink(missing_ok=True)
        return False

    # Verify it's actually an opencode serve process
    cmd = runtime.get_process_command(pid)
    if not cmd:
        logger.debug("Failed to verify OpenCode process (pid=%s): command not available", pid)
        return False
    if "opencode" not in cmd or "serve" not in cmd:
        return False

    if runtime.stop_pid(pid, timeout=5):
        pid_file.unlink(missing_ok=True)
        return True
    logger.warning("Failed to stop OpenCode server (pid=%s)", pid)
    return False


def cmd_stop():
    runtime.stop_service()
    runtime.stop_ui()

    # Also terminate OpenCode server on full stop
    if _stop_opencode_server():
        print("OpenCode server stopped")

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
    print(
        f"  \033[32m{summary.get('pass', 0)} passed\033[0m  "
        f"\033[33m{summary.get('warn', 0)} warnings\033[0m  "
        f"\033[31m{summary.get('fail', 0)} failed\033[0m"
    )
    print()

    return 0 if result["ok"] else 1


def cmd_version():
    """Show current version."""
    print(f"vibe-remote {__version__}")
    return 0


def get_latest_version() -> dict:
    """Fetch latest version info from PyPI.

    Returns:
        {"current": str, "latest": str, "has_update": bool, "error": str|None}
    """
    return get_latest_version_info(__version__)


def cmd_check_update():
    """Check for available updates."""
    print(f"Current version: {__version__}")
    print("Checking for updates...")

    info = get_latest_version()

    if info["error"]:
        print(f"\033[33mFailed to check for updates: {info['error']}\033[0m")
        return 1

    if info["has_update"]:
        print(f"\033[32mNew version available: {info['latest']}\033[0m")
        print(f"\nRun '\033[1mvibe upgrade\033[0m' to update.")
    else:
        print("\033[32mYou are using the latest version.\033[0m")

    return 0


def cmd_upgrade():
    """Upgrade vibe-remote to the latest version."""
    print(f"Current version: {__version__}")
    print("Checking for updates...")

    info = get_latest_version()

    if info["error"]:
        print(f"\033[33mFailed to check for updates: {info['error']}\033[0m")
        print("Attempting upgrade anyway...")
    elif not info["has_update"]:
        print("\033[32mYou are already using the latest version.\033[0m")
        return 0
    else:
        print(f"New version available: {info['latest']}")

    print("\nUpgrading...")

    current_vibe_path = cache_running_vibe_path()
    plan = build_upgrade_plan(vibe_path=current_vibe_path)
    print(f"Using {plan.method}: {' '.join(plan.command)}")

    # Use a stable directory as cwd to avoid issues when running from a
    # directory that uv may delete during upgrade (e.g. inside the uv tool venv).
    safe_cwd = get_safe_cwd()

    try:
        result = subprocess.run(plan.command, capture_output=True, text=True, env=plan.env, cwd=safe_cwd)
        if result.returncode == 0:
            print("\033[32mUpgrade successful!\033[0m")
            print("Please restart vibe to use the new version:")
            print("  vibe stop && vibe")
            return 0
        else:
            print(f"\033[31mUpgrade failed:\033[0m\n{result.stderr}")
            return 1
    except Exception as e:
        print(f"\033[31mUpgrade failed: {e}\033[0m")
        return 1


def cmd_restart():
    """Restart all services (stop + start)."""
    print("Restarting vibe services...")
    cmd_stop()
    print("Waiting 3 seconds...")
    time.sleep(3)
    return cmd_vibe()


def build_parser():
    parser = VibeArgumentParser(prog="vibe")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("stop", help="Stop all services")
    subparsers.add_parser("restart", help="Restart all services")
    subparsers.add_parser("status", help="Show service status")
    subparsers.add_parser("doctor", help="Run diagnostics")
    subparsers.add_parser("version", help="Show version")
    subparsers.add_parser("check-update", help="Check for updates")
    subparsers.add_parser("upgrade", help="Upgrade to latest version")

    task_parser = subparsers.add_parser(
        "task",
        help="Manage scheduled tasks",
        description="Create, inspect, and control scheduled prompts for Vibe Remote.",
        epilog=_task_examples_text(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe task --help",
        error_hint="Run one of the task subcommands below. Use 'vibe task add --help' for task creation details.",
    )
    task_subparsers = task_parser.add_subparsers(
        dest="task_command",
        metavar="{add,list,show,pause,resume,remove}",
    )
    task_subparsers.required = True

    task_add_parser = task_subparsers.add_parser(
        "add",
        help="Create a scheduled task",
        description="Create a recurring or one-shot scheduled prompt.",
        epilog=_task_add_examples_text(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe task add --help",
        error_hint="Use --session-key together with exactly one schedule flag and one prompt input flag.",
    )
    task_add_parser.add_argument(
        "--session-key",
        required=True,
        help="Target session key. Prefer a threadless key unless the task must stay in one thread.",
    )
    schedule_group = task_add_parser.add_mutually_exclusive_group(required=True)
    schedule_group.add_argument("--cron", help="Recurring schedule in 5-field crontab format")
    schedule_group.add_argument("--at", help="One-shot timestamp in ISO 8601 format")
    prompt_group = task_add_parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="Prompt text to send")
    prompt_group.add_argument("--prompt-file", help="Read prompt text from a UTF-8 text file")
    task_add_parser.add_argument("--timezone", help="IANA timezone name used for --cron and naive --at values")

    task_subparsers.add_parser(
        "list",
        help="List scheduled tasks",
        description="List all stored scheduled tasks.",
        epilog="Use the returned task IDs with 'vibe task show', 'vibe task pause', 'vibe task resume', or 'vibe task remove'.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe task list --help",
    )
    task_list_parser = task_subparsers.choices["list"]
    _add_hidden_task_alias(task_subparsers, "ls", task_list_parser)

    task_show_parser = task_subparsers.add_parser(
        "show",
        help="Show a scheduled task",
        description="Show one scheduled task by ID.",
        epilog="Find task IDs with: vibe task list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe task show --help",
    )
    task_show_parser.add_argument("task_id", help="Task ID from 'vibe task list'")

    task_pause_parser = task_subparsers.add_parser(
        "pause",
        help="Pause a scheduled task",
        description="Disable one scheduled task without deleting it.",
        epilog="Find task IDs with: vibe task list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe task pause --help",
    )
    task_pause_parser.add_argument("task_id", help="Task ID from 'vibe task list'")

    task_resume_parser = task_subparsers.add_parser(
        "resume",
        help="Resume a scheduled task",
        description="Re-enable one paused scheduled task.",
        epilog="Find task IDs with: vibe task list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe task resume --help",
    )
    task_resume_parser.add_argument("task_id", help="Task ID from 'vibe task list'")

    task_rm_parser = task_subparsers.add_parser(
        "remove",
        help="Remove a scheduled task",
        description="Delete one scheduled task permanently.",
        epilog="Find task IDs with: vibe task list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe task remove --help",
    )
    task_rm_parser.add_argument("task_id", help="Task ID from 'vibe task list'")
    _add_hidden_task_alias(task_subparsers, "rm", task_rm_parser)
    return parser


def main():
    cache_running_vibe_path()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "stop":
        sys.exit(cmd_stop())
    if args.command == "restart":
        sys.exit(cmd_restart())
    if args.command == "status":
        sys.exit(cmd_status())
    if args.command == "doctor":
        sys.exit(cmd_doctor())
    if args.command == "version":
        sys.exit(cmd_version())
    if args.command == "check-update":
        sys.exit(cmd_check_update())
    if args.command == "upgrade":
        sys.exit(cmd_upgrade())
    if args.command == "task":
        if args.task_command == "add":
            sys.exit(cmd_task_add(args))
        if args.task_command in {"list", "ls"}:
            sys.exit(cmd_task_list())
        if args.task_command == "show":
            sys.exit(cmd_task_show(args.task_id))
        if args.task_command == "pause":
            sys.exit(cmd_task_set_enabled(args.task_id, False))
        if args.task_command == "resume":
            sys.exit(cmd_task_set_enabled(args.task_id, True))
        if args.task_command in {"remove", "rm"}:
            sys.exit(cmd_task_remove(args.task_id))
        parser.error("task command is required")
    sys.exit(cmd_vibe())
