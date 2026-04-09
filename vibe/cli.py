import argparse
import json
import logging
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from tzlocal import get_localzone_name

from config import SettingsStore, paths
from config.v2_config import (
    AgentsConfig,
    ClaudeConfig,
    CodexConfig,
    OpenCodeConfig,
    RuntimeConfig,
    SlackConfig,
    V2Config,
)
from core.scheduled_tasks import ScheduledTaskStore, TaskExecutionStore, parse_session_key
from core.watches import (
    DEFAULT_RETRY_EXIT_CODE,
    WATCH_RECONCILE_INTERVAL_SECONDS,
    ManagedWatchStore,
    WatchRuntimeStateStore,
)
from vibe import __version__, api, runtime
from vibe.upgrade import (
    build_upgrade_plan,
    cache_running_vibe_path,
    get_latest_version_info,
    get_restart_command,
    get_restart_environment,
    get_safe_cwd,
)

logger = logging.getLogger(__name__)

WATCH_STARTUP_STABLE_RUNNING_SECONDS = 1.5
WATCH_STARTUP_JITTER_BUFFER_SECONDS = 1.0


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


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("must be finite")
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def _task_examples_text() -> str:
    return dedent(
        """\
        Examples:
          vibe task add --session-key 'slack::channel::C123' --cron '0 * * * *' --prompt 'Share the hourly summary.'
          vibe task update 12ab34cd56ef --cron '*/30 * * * *' --name 'Half-hour summary'
          vibe task run 12ab34cd56ef
          vibe task add --session-key 'discord::user::123456789' --at '2026-03-31T09:00:00+08:00' --prompt-file briefing.md
          vibe task add --session-key 'slack::channel::C123::thread::171717.123' --post-to channel --cron '*/5 * * * *' --prompt 'Tell a new joke each time.'
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
          If this is your first time using this command, read this whole help entry before creating a task.
          `--session-key` chooses which session Vibe Remote will continue using when the task runs.
          Keep the current session key when future runs should stay in the same session.
          When you want to leave the current thread session and start or reuse the higher-level session instead, use the higher-level key. Example:
            slack::channel::C123::thread::171717.123  -> keep the current thread session
            slack::channel::C123                      -> create or reuse the channel-scoped session
          `--post-to channel` changes where the message is posted, not which session is continued.
          Use --deliver-key only when delivery must go to a different explicit target.
          `--prompt` and `--prompt-file` provide the stored task content that will be injected each time the task runs.
          Use --cron for recurring jobs and --at for one-shot jobs.
          --timezone controls how --cron and naive --at timestamps are interpreted.

        Examples:
          vibe task add --session-key 'slack::channel::C123' --cron '0 * * * *' --prompt 'Share the hourly summary.'
          vibe task add --session-key 'slack::channel::C123::thread::171717.123' --post-to channel --cron '*/5 * * * *' --prompt 'Tell a new joke each time.'
          vibe task add --session-key 'slack::channel::C123::thread::171717.123' --deliver-key 'slack::channel::C999' --cron '0 9 * * *' --prompt 'Post the daily summary in the announcements channel.'
          vibe task add --session-key 'discord::user::123456789' --at '2026-03-31T09:00:00+08:00' --prompt 'Send the release reminder.'
          vibe task add --session-key 'lark::channel::oc_abc::thread::om_123' --cron '30 9 * * 1-5' --timezone 'Asia/Shanghai' --prompt-file standup.txt
        """
    )


def _task_update_examples_text() -> str:
    return dedent(
        """\
        You may update any subset of the stored task fields while keeping the same task ID.

        Common updates:
          vibe task update 12ab34cd56ef --name 'Morning summary'
          vibe task update 12ab34cd56ef --cron '*/30 * * * *'
          vibe task update 12ab34cd56ef --prompt 'Send a shorter summary.'
          vibe task update 12ab34cd56ef --session-key 'slack::channel::C123::thread::171717.123' --post-to channel
          vibe task update 12ab34cd56ef --deliver-key 'slack::channel::C999'
          vibe task update 12ab34cd56ef --reset-delivery

        Guidance:
          Unspecified fields keep their existing values.
          Use --reset-delivery to return to following --session-key directly.
          When changing schedule fields, pass either --cron or --at.
          Use --clear-name if you want the task to stop storing a custom name.
        """
    )


def _hook_send_examples_text() -> str:
    return dedent(
        """\
        Session key format:
          <platform>::channel::<channel_id>
          <platform>::user::<user_id>
          <platform>::channel::<channel_id>::thread::<thread_id>
          <platform>::user::<user_id>::thread::<thread_id>

        Guidance:
          If this is your first time using this command, read this whole help entry before queuing a hook.
          `vibe hook send` queues one asynchronous turn without persisting a scheduled task.
          `--session-key` chooses which session Vibe Remote will continue using for that one async turn.
          Keep the current session key when the hook should continue in the same session.
          When you want to leave the current thread session and start or reuse the higher-level session instead, use the higher-level key. Example:
            slack::channel::C123::thread::171717.123  -> keep the current thread session
            slack::channel::C123                      -> create or reuse the channel-scoped session
          `--post-to channel` changes where the message is posted, not which session is continued.
          Use --deliver-key only when delivery must go to a different explicit target.
          `--prompt` and `--prompt-file` provide the one-shot async content that will be queued immediately.

        Examples:
          vibe hook send --session-key 'slack::channel::C123' --prompt 'The export finished. Share the summary.'
          vibe hook send --session-key 'slack::channel::C123::thread::171717.123' --post-to channel --prompt 'Share the benchmark result in the channel.'
          vibe hook send --session-key 'slack::channel::C123' --deliver-key 'slack::channel::C999' --prompt 'Post the deployment summary in announcements.'
          vibe hook send --session-key 'discord::user::123456789' --prompt-file release-note.txt
          vibe hook send --session-key 'lark::channel::oc_abc::thread::om_123' --prompt 'Post the benchmark result in this thread.'
        """
    )


def _watch_examples_text() -> str:
    return dedent(
        """\
        Examples:
          vibe watch add --session-key 'slack::channel::C123' --cwd /path/to/repo --name 'Wait for export' --shell 'python3 scripts/wait_for_export.py'
          vibe watch add --session-key 'slack::channel::C123::thread::171717.123' --cwd /path/to/repo --post-to channel --prefix 'The CI job finished.' -- python3 scripts/wait_for_ci.py --build 42
          vibe watch add --session-key 'slack::channel::C123' --forever --retry-exit-code 75 --retry-delay 60 -- uv run --no-project /path/to/repo/skills/background-watch-hook/scripts/wait_pr.py --repo cyhhao/vibe-remote --pr 153
          vibe watch list --brief
          vibe watch show 12ab34cd56ef
          vibe watch pause 12ab34cd56ef
        """
    )


def _watch_add_examples_text() -> str:
    return dedent(
        """\
        Session key format:
          <platform>::channel::<channel_id>
          <platform>::user::<user_id>
          <platform>::channel::<channel_id>::thread::<thread_id>
          <platform>::user::<user_id>::thread::<thread_id>

        Guidance:
          If this is your first time using this command, read this whole help entry before creating a watch.
          Use a watch when a script should wait in the background and send a follow-up when it detects an event or reaches a terminal failure.
          `--session-key` chooses which session Vibe Remote will continue using for follow-up messages from the watch.
          Keep the current session key when follow-up should continue in the same session.
          When you want to leave the current thread session and start or reuse the higher-level session instead, use the higher-level key. Example:
            slack::channel::C123::thread::171717.123  -> keep the current thread session
            slack::channel::C123                      -> create or reuse the channel-scoped session
          `--post-to channel` changes where the follow-up is posted, not which session is continued.
          Use --deliver-key only when delivery must go to a different explicit target.
          `--prefix` becomes the instruction text of the follow-up hook. On a successful cycle, Vibe Remote prepends `--prefix` before waiter stdout and joins them with a blank line when both exist.
          Terminal failures also send a follow-up and disable the watch.
          In forever mode, failures are retried only when the waiter exits with an allowed `--retry-exit-code`.
          Pass either --shell '<command>' or a command after '--'.
          Creation-time script checks resolve relative script paths from --cwd when provided, otherwise from the current CLI working directory.
          If the managed watch must run from a different directory later, prefer an absolute script path or set --cwd explicitly.
          --timeout applies to each cycle. --lifetime-timeout applies only to the whole forever watch lifetime.

        Examples:
          vibe watch add --session-key 'slack::channel::C123' --cwd /path/to/repo --shell 'python3 scripts/wait_for_export.py'
          vibe watch add --session-key 'slack::channel::C123::thread::171717.123' --post-to channel --prefix 'The export finished.' -- bash -lc 'sleep 120; echo done'
          vibe watch add --session-key 'slack::channel::C123' --forever --timeout 600 --lifetime-timeout 86400 --retry-exit-code 75 --retry-delay 30 -- uv run --no-project /path/to/repo/skills/background-watch-hook/scripts/wait_pr.py --repo cyhhao/vibe-remote --pr 153
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


def _resolve_prompt_input(args, *, help_command: str, example_command: str) -> str:
    prompt = (getattr(args, "prompt", None) or "").strip()
    prompt_file = getattr(args, "prompt_file", None)
    if prompt and prompt_file:
        raise TaskCliError(
            "use either --prompt or --prompt-file",
            code="conflicting_prompt_inputs",
            hint="Pass inline text with --prompt or load it from disk with --prompt-file, but not both.",
            help_command=help_command,
        )
    if prompt:
        return prompt
    if getattr(args, "prompt", None) is not None:
        raise TaskCliError(
            "prompt text cannot be empty",
            code="empty_prompt",
            hint="Provide non-empty text after --prompt, or use --prompt-file with a readable text file.",
            help_command=help_command,
        )
    if prompt_file:
        try:
            content = Path(prompt_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise TaskCliError(
                f"failed to read prompt file: {exc}",
                code="prompt_file_read_failed",
                hint="Use --prompt-file with a readable UTF-8 text file.",
                example=f"{example_command} --prompt-file briefing.md",
                help_command=help_command,
                details={"prompt_file": prompt_file},
            ) from exc
        if not content:
            raise TaskCliError(
                "prompt file is empty",
                code="empty_prompt",
                hint="Put the prompt text in the file, or pass it directly with --prompt.",
                example=f"{example_command} --prompt 'Share the hourly summary.'",
                help_command=help_command,
                details={"prompt_file": prompt_file},
            )
        return content
    raise TaskCliError(
        "one of --prompt or --prompt-file is required",
        code="missing_prompt",
        hint="Pass inline text with --prompt or load it from disk with --prompt-file.",
        help_command=help_command,
    )


def _normalize_run_at(value: str, timezone_name: str) -> str:
    dt = datetime.fromisoformat(value)
    tz = ZoneInfo(timezone_name)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt.isoformat()


def _normalize_task_name(value: Optional[str], *, allow_none: bool = True) -> Optional[str]:
    if value is None:
        return None if allow_none else ""
    normalized = value.strip()
    if not normalized:
        raise TaskCliError(
            "task name cannot be empty",
            code="empty_task_name",
            hint="Pass a short non-empty name, or omit --name.",
        )
    return normalized


def _normalize_watch_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise TaskCliError(
            "watch name cannot be empty",
            code="empty_watch_name",
            hint="Pass a short non-empty name, or omit --name.",
            help_command="vibe watch add --help",
        )
    return normalized


def _task_prompt_preview(prompt: str, *, max_chars: int = 72) -> str:
    compact = " ".join((prompt or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"


def _task_display_name(task) -> str:
    return task.name or _task_prompt_preview(task.prompt)


def _task_state(task) -> str:
    if task.enabled:
        return "active"
    if _is_completed_one_shot(task):
        return "completed"
    return "paused"


def _task_last_status(task) -> str:
    if task.last_run_at and task.last_error:
        return "failed"
    if task.last_run_at:
        return "succeeded"
    return "never_run"


def _task_next_run_at(task) -> Optional[str]:
    if not task.enabled:
        return None
    try:
        timezone = ZoneInfo(task.timezone)
        now = datetime.now(timezone)
        if task.schedule_type == "cron":
            if not task.cron:
                return None
            trigger = CronTrigger.from_crontab(task.cron, timezone=timezone)
        elif task.schedule_type == "at":
            if not task.run_at:
                return None
            run_at = datetime.fromisoformat(task.run_at)
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone)
            else:
                run_at = run_at.astimezone(timezone)
            trigger = DateTrigger(run_date=run_at)
        else:
            return None
        next_fire = trigger.get_next_fire_time(None, now)
        return next_fire.isoformat() if next_fire else None
    except Exception:
        return None


def _task_schedule_summary(task) -> str:
    if task.schedule_type == "cron":
        return f"cron:{task.cron}" if task.cron else "cron"
    if task.schedule_type == "at":
        return f"at:{task.run_at}" if task.run_at else "at"
    return task.schedule_type


def _task_next_run_sort_key(task):
    next_run_at = _task_next_run_at(task)
    if not next_run_at:
        return (True, datetime.max.replace(tzinfo=timezone.utc))
    try:
        instant = datetime.fromisoformat(next_run_at)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        else:
            instant = instant.astimezone(timezone.utc)
    except ValueError:
        return (True, datetime.max.replace(tzinfo=timezone.utc))
    return (False, instant)


def _task_payload(task, *, brief: bool = False):
    derived = {
        "display_name": _task_display_name(task),
        "prompt_preview": _task_prompt_preview(task.prompt),
        "state": _task_state(task),
        "last_status": _task_last_status(task),
        "next_run_at": _task_next_run_at(task),
        "schedule_summary": _task_schedule_summary(task),
    }
    if brief:
        return {
            "id": task.id,
            "name": task.name,
            "display_name": derived["display_name"],
            "state": derived["state"],
            "last_status": derived["last_status"],
            "next_run_at": derived["next_run_at"],
            "schedule_type": task.schedule_type,
            "schedule_summary": derived["schedule_summary"],
            "session_key": task.session_key,
            "post_to": task.post_to,
            "deliver_key": task.deliver_key,
            "timezone": task.timezone,
            "enabled": task.enabled,
        }
    payload = task.to_dict()
    payload.update(derived)
    return payload


def _sort_tasks_for_display(tasks):
    return sorted(
        tasks,
        key=lambda item: (
            *_task_next_run_sort_key(item),
            item.created_at,
            item.id,
        ),
    )


def _task_store() -> ScheduledTaskStore:
    return ScheduledTaskStore()


def _task_request_store() -> TaskExecutionStore:
    return TaskExecutionStore()


def _watch_store() -> ManagedWatchStore:
    return ManagedWatchStore()


def _watch_runtime_store() -> WatchRuntimeStateStore:
    return WatchRuntimeStateStore()


def _supported_task_platforms() -> set[str]:
    try:
        config = _ensure_config()
    except Exception:
        return set()
    enabled = getattr(config, "enabled_platforms", None)
    if callable(enabled):
        return set(enabled())
    return {getattr(config, "platform", "slack")}


def _is_completed_one_shot(task) -> bool:
    return task.schedule_type == "at" and not task.enabled and bool(task.last_run_at)


def _parse_validated_session_key(
    session_key: str,
    *,
    help_command: str,
) -> object:
    try:
        parsed = parse_session_key(session_key)
    except ValueError as exc:
        raise TaskCliError(
            str(exc),
            code="invalid_session_key",
            hint="Use <platform>::<channel|user>::<id>[::thread::<thread_id>]. Prefer a threadless key unless the command must reply in one specific thread.",
            example="slack::channel::C123",
            help_command=help_command,
            details={"session_key": session_key},
        ) from exc

    supported_platforms = _supported_task_platforms()
    if parsed.platform not in supported_platforms:
        supported_text = ", ".join(sorted(supported_platforms)) or "none"
        raise TaskCliError(
            f"unsupported task platform: {parsed.platform}",
            code="unsupported_platform",
            hint="Choose a platform that is enabled in Vibe Remote before sending the request.",
            example="slack::channel::C123",
            help_command=help_command,
            details={
                "requested_platform": parsed.platform,
                "configured_platforms": sorted(supported_platforms),
                "configured_platforms_text": supported_text,
            },
        )
    return parsed


def _validate_delivery_args(
    *,
    session_key: str,
    post_to: Optional[str],
    deliver_key: Optional[str],
    help_command: str,
):
    if post_to and deliver_key:
        raise TaskCliError(
            "use either --post-to or --deliver-key, not both",
            code="conflicting_delivery_target",
            hint="Use --post-to for the common thread/channel delivery choice, or --deliver-key for an explicit delivery target.",
            help_command=help_command,
        )

    session_target = _parse_validated_session_key(session_key, help_command=help_command)
    delivery_target = None
    if deliver_key:
        delivery_target = _parse_validated_session_key(deliver_key, help_command=help_command)
        if delivery_target.platform != session_target.platform:
            raise TaskCliError(
                "--deliver-key must use the same platform as --session-key",
                code="invalid_delivery_target",
                hint="Keep session memory and delivery on the same IM platform. Change only the channel, user, or thread target.",
                help_command=help_command,
                details={
                    "session_platform": session_target.platform,
                    "delivery_platform": delivery_target.platform,
                },
            )
    elif post_to == "thread" and not session_target.thread_id:
        raise TaskCliError(
            "--post-to thread requires a thread-bound --session-key or an explicit --deliver-key",
            code="invalid_delivery_target",
            hint="Append ::thread::<thread_id> to --session-key, or use --deliver-key with a thread target.",
            help_command=help_command,
            details={"session_key": session_key, "post_to": post_to},
        )
    return session_target, delivery_target


def _collect_target_warnings(*targets) -> list[dict]:
    store = SettingsStore.get_instance(paths.get_settings_path())
    warnings: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for target in targets:
        if target is None:
            continue
        dedupe_key = (target.platform, target.scope_type, target.scope_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        if target.platform == "lark" and target.is_dm:
            bound_user = store.get_user(target.scope_id, platform="lark")
            if bound_user is None:
                warnings.append(
                    {
                        "code": "lark_user_not_bound",
                        "message": "The target Lark user is not bound in Vibe Remote yet; delivery may fail at runtime.",
                        "details": {"session_key": target.to_key(include_thread=False)},
                    }
                )
            elif not getattr(bound_user, "dm_chat_id", ""):
                warnings.append(
                    {
                        "code": "lark_dm_chat_unbound",
                        "message": "The target Lark user has no dm_chat_id binding yet; delivery may fail at runtime.",
                        "details": {"session_key": target.to_key(include_thread=False)},
                    }
                )

    return warnings


def _resolve_watch_command(args, *, help_command: str) -> tuple[list[str], Optional[str]]:
    shell_command = (getattr(args, "shell", None) or "").strip()
    raw_command = list(getattr(args, "waiter_command", []) or [])
    if raw_command and raw_command[0] == "--":
        raw_command = raw_command[1:]

    if shell_command and raw_command:
        raise TaskCliError(
            "use either --shell or a command after '--', not both",
            code="conflicting_watch_command_inputs",
            hint="Pass a shell string with --shell, or pass the executable and its args after '--'.",
            help_command=help_command,
        )
    if shell_command:
        return [], shell_command
    if raw_command:
        return raw_command, None
    raise TaskCliError(
        "one of --shell or a command after '--' is required",
        code="missing_watch_command",
        hint="Pass a shell command with --shell, or add the watcher executable and its args after '--'.",
        help_command=help_command,
    )


def _looks_like_script_path(token: str) -> bool:
    if not token or token == "--" or token.startswith("-"):
        return False
    path = Path(token)
    return path.is_absolute() or "/" in token or "\\" in token


_PYTHON_RUNNER_RE = re.compile(r"^python(?:\d+(?:\.\d+)*)?$")


UV_RUN_OPTIONS_WITH_VALUES = {
    "--extra",
    "--no-extra",
    "--group",
    "--no-group",
    "--only-group",
    "--env-file",
    "--with",
    "--with-editable",
    "--with-requirements",
    "--package",
    "--python-platform",
    "--index",
    "--default-index",
    "--index-url",
    "--extra-index-url",
    "--find-links",
    "--index-strategy",
    "--keyring-provider",
    "--upgrade-package",
    "--resolution",
    "--prerelease",
    "--fork-strategy",
    "--exclude-newer",
    "--exclude-newer-package",
    "--refresh-package",
    "--reinstall-package",
    "--link-mode",
    "--config-setting",
    "--config-settings-package",
    "--no-build-isolation-package",
    "--no-build-package",
    "--no-binary-package",
    "--cache-dir",
    "--python",
    "--color",
    "--allow-insecure-host",
    "--directory",
    "--project",
    "--config-file",
}

UV_RUN_SHORT_OPTIONS_WITH_VALUES = {"-w", "-i", "-f", "-P", "-C", "-p"}

PYTHON_OPTIONS_WITH_VALUES = {"-W", "-X"}
PYTHON_OPTIONS_WITHOUT_SCRIPT = {"-c", "-m"}
PYTHON_LONG_OPTIONS_WITH_VALUES = {"--check-hash-based-pycs"}
SHELL_LONG_OPTIONS_WITH_VALUES = {"--init-file", "--rcfile"}
SHELL_SHORT_OPTIONS_WITH_VALUES = {"-o", "-O"}


def _split_uv_run_command(tokens: list[str]) -> tuple[list[str], str | None]:
    effective_cwd: str | None = None
    index = 2
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return tokens[index + 1 :], effective_cwd
        if token == "--directory":
            if index + 1 < len(tokens):
                effective_cwd = tokens[index + 1]
            index += 2
            continue
        if token.startswith("--directory="):
            effective_cwd = token.split("=", 1)[1]
            index += 1
            continue
        if token in UV_RUN_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if token in UV_RUN_SHORT_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token.startswith("-") and len(token) > 2 and token[:2] in UV_RUN_SHORT_OPTIONS_WITH_VALUES:
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return tokens[index:], effective_cwd
    return [], effective_cwd


def _split_uv_command(tokens: list[str]) -> tuple[str | None, list[str], str | None]:
    effective_cwd: str | None = None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return None, [], effective_cwd
        if token == "run":
            return token, [tokens[0], *tokens[index:]], effective_cwd
        if token == "--directory":
            if index + 1 < len(tokens):
                effective_cwd = tokens[index + 1]
            index += 2
            continue
        if token.startswith("--directory="):
            effective_cwd = token.split("=", 1)[1]
            index += 1
            continue
        if token in UV_RUN_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if token in UV_RUN_SHORT_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token.startswith("-") and len(token) > 2 and token[:2] in UV_RUN_SHORT_OPTIONS_WITH_VALUES:
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token, tokens[index:], effective_cwd
    return None, [], effective_cwd


def _extract_python_script_path(tokens: list[str]) -> str | None:
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token in PYTHON_OPTIONS_WITHOUT_SCRIPT:
            return None
        if any(token.startswith(option) and token != option for option in PYTHON_OPTIONS_WITHOUT_SCRIPT):
            return None
        if token in PYTHON_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if token in PYTHON_LONG_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if any(token.startswith(f"{option}=") for option in PYTHON_OPTIONS_WITH_VALUES):
            index += 1
            continue
        if any(token.startswith(f"{option}=") for option in PYTHON_LONG_OPTIONS_WITH_VALUES):
            index += 1
            continue
        if any(token.startswith(option) and token != option for option in PYTHON_OPTIONS_WITH_VALUES):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        break

    if index < len(tokens) and tokens[index] != "-":
        return tokens[index]
    return None


def _extract_watch_script_probe_from_tokens(tokens: list[str]) -> tuple[str | None, str | None]:
    if not tokens:
        return None, None

    runner = Path(tokens[0]).name
    if _PYTHON_RUNNER_RE.fullmatch(runner):
        return _extract_python_script_path(tokens), None

    if runner in {"bash", "sh"}:
        return _extract_shell_script_path(tokens), None

    if runner == "uv":
        subcommand, uv_tokens, global_cwd = _split_uv_command(tokens)
        if subcommand == "run":
            inner_tokens, effective_cwd = _split_uv_run_command(uv_tokens)
            script_path, nested_cwd = _extract_watch_script_probe_from_tokens(inner_tokens)
            return script_path, nested_cwd or effective_cwd or global_cwd

    if _looks_like_script_path(tokens[0]):
        return tokens[0], None

    return None, None


def _extract_shell_script_path(tokens: list[str]) -> str | None:
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-c":
            return None
        if token in SHELL_LONG_OPTIONS_WITH_VALUES or token in SHELL_SHORT_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if any(token.startswith(f"{option}=") for option in SHELL_LONG_OPTIONS_WITH_VALUES):
            index += 1
            continue
        if token.startswith("-") and not token.startswith("--") and "c" in token[1:]:
            return None
        if token.startswith("-"):
            index += 1
            continue
        break

    if index < len(tokens) and tokens[index] != "-":
        return tokens[index]
    return None

def _resolve_watch_preflight_base_dir(
    cwd: str | None,
    probe_cwd: str | None,
) -> Path:
    base_dir = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    if not probe_cwd:
        return base_dir

    probe_base = Path(probe_cwd)
    if not probe_base.is_absolute():
        probe_base = (base_dir / probe_base).resolve()
    return probe_base


def _resolve_watch_script_probe(
    command: list[str],
) -> tuple[str | None, str | None]:
    return _extract_watch_script_probe_from_tokens(command)


def _validate_watch_script_preflight(
    command: list[str],
    shell_command: str | None,
    *,
    cwd: str | None,
    help_command: str,
) -> None:
    if shell_command:
        return

    script_path, probe_cwd = _resolve_watch_script_probe(command)
    if not script_path:
        return

    base_dir = _resolve_watch_preflight_base_dir(
        cwd,
        probe_cwd,
    )
    checked_from = None if Path(script_path).is_absolute() else str(base_dir)
    candidate = Path(script_path)
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    if candidate.is_file():
        return

    hint = "Fix the script path or use an absolute path."
    if checked_from is not None:
        hint = (
            "Fix the script path, create the watch from the directory that contains it, "
            "pass --cwd, or use an absolute path."
        )

    raise TaskCliError(
        f"watch script not found: {script_path}",
        code="invalid_watch_script",
        hint=hint,
        help_command=help_command,
        details={
            "script": script_path,
            "resolved_path": str(candidate),
            "checked_from": checked_from,
        },
    )


def _watch_command_preview(watch, *, max_chars: int = 120) -> str:
    preview = watch.shell_command or shlex.join(watch.command)
    preview = preview.strip()
    if len(preview) <= max_chars:
        return preview
    return preview[: max_chars - 1].rstrip() + "…"


def _watch_display_name(watch) -> str:
    return watch.name or _watch_command_preview(watch)


def _watch_state(watch, runtime_entry: Optional[dict[str, object]]) -> str:
    if runtime_entry and runtime_entry.get("running"):
        return "running"
    if watch.enabled and watch.mode == "forever":
        return "armed"
    if watch.enabled:
        return "pending"
    if watch.last_error:
        return "failed"
    if watch.last_event_at:
        return "completed"
    return "paused"


def _watch_payload(watch, runtime_entry: Optional[dict[str, object]], *, brief: bool = False) -> dict:
    derived = {
        "display_name": _watch_display_name(watch),
        "command_preview": _watch_command_preview(watch),
        "state": _watch_state(watch, runtime_entry),
        "runtime": runtime_entry or {},
    }
    if brief:
        return {
            "id": watch.id,
            "name": watch.name,
            "display_name": derived["display_name"],
            "state": derived["state"],
            "mode": watch.mode,
            "session_key": watch.session_key,
            "timeout_seconds": watch.timeout_seconds,
            "lifetime_timeout_seconds": watch.lifetime_timeout_seconds,
            "enabled": watch.enabled,
            "last_event_at": watch.last_event_at,
            "last_error": watch.last_error,
        }
    payload = watch.to_dict()
    payload.update(derived)
    return payload


def _seconds_since_iso(timestamp: object) -> float | None:
    if not isinstance(timestamp, str) or not timestamp.strip():
        return None
    try:
        started_at = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())


def _default_watch_startup_timeout_seconds(*, stable_running_seconds: float = WATCH_STARTUP_STABLE_RUNNING_SECONDS) -> float:
    return WATCH_RECONCILE_INTERVAL_SECONDS + stable_running_seconds + WATCH_STARTUP_JITTER_BUFFER_SECONDS


def _wait_for_watch_startup(
    store: ManagedWatchStore,
    runtime_store: WatchRuntimeStateStore,
    watch_id: str,
    *,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = 0.1,
    stable_running_seconds: float = WATCH_STARTUP_STABLE_RUNNING_SECONDS,
):
    inspect_command = f"vibe watch show {watch_id}"
    if timeout_seconds is None:
        timeout_seconds = _default_watch_startup_timeout_seconds(stable_running_seconds=stable_running_seconds)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        store.maybe_reload()
        watch = store.get_watch(watch_id)
        if watch is None:
            raise TaskCliError(
                f"watch '{watch_id}' could not be verified because it disappeared during startup",
                code="watch_startup_failed",
                hint="Recreate the watch, then inspect its first-cycle state before reporting that monitoring is active.",
                example=inspect_command,
                help_command=inspect_command,
                details={"watch_id": watch_id},
            )
        runtime_entry = runtime_store.load().get("watches", {}).get(watch_id)
        if watch.last_error and not watch.enabled:
            raise TaskCliError(
                f"watch '{watch.name or watch.id}' failed during startup and has already been disabled",
                code="watch_startup_failed",
                hint="Inspect the stored watch error, fix the waiter or its dependencies, then recreate the watch if monitoring should continue.",
                example=inspect_command,
                help_command=inspect_command,
                details={"watch": _watch_payload(watch, runtime_entry)},
            )
        if watch.mode == "once" and watch.last_finished_at and not watch.last_error and watch.last_exit_code == 0:
            return watch, runtime_entry
        if runtime_entry and runtime_entry.get("running"):
            stable_for = _seconds_since_iso(runtime_entry.get("started_at")) or _seconds_since_iso(watch.last_started_at)
            if stable_for is not None and stable_for >= stable_running_seconds:
                return watch, runtime_entry
        time.sleep(poll_interval_seconds)

    store.maybe_reload()
    watch = store.get_watch(watch_id)
    runtime_entry = runtime_store.load().get("watches", {}).get(watch_id)
    if watch is not None and watch.last_error and not watch.enabled:
        raise TaskCliError(
            f"watch '{watch.name or watch.id}' failed during startup and has already been disabled",
            code="watch_startup_failed",
            hint="Inspect the stored watch error, fix the waiter or its dependencies, then recreate the watch if monitoring should continue.",
            example=inspect_command,
            help_command=inspect_command,
            details={"watch": _watch_payload(watch, runtime_entry)},
        )
    raise TaskCliError(
        f"watch '{watch_id}' was created but startup was not confirmed within {timeout_seconds:.0f} second(s)",
        code="watch_startup_unconfirmed",
        hint="Confirm that the Vibe Remote service is running, then inspect the watch state before reporting that monitoring is active.",
        example=inspect_command,
        help_command=inspect_command,
        details={"watch": _watch_payload(watch, runtime_entry) if watch is not None else {"id": watch_id}},
    )


def cmd_task_add(args):
    try:
        session_target, delivery_target = _validate_delivery_args(
            session_key=args.session_key,
            post_to=getattr(args, "post_to", None),
            deliver_key=getattr(args, "deliver_key", None),
            help_command="vibe task add --help",
        )
        prompt = _resolve_prompt_input(
            args,
            help_command="vibe task add --help",
            example_command="vibe task add --session-key 'slack::channel::C123' --cron '0 * * * *'",
        )
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
                name=_normalize_task_name(getattr(args, "name", None)),
                session_key=args.session_key,
                post_to=args.post_to,
                deliver_key=args.deliver_key,
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
                name=_normalize_task_name(getattr(args, "name", None)),
                session_key=args.session_key,
                post_to=args.post_to,
                deliver_key=args.deliver_key,
                prompt=prompt,
                schedule_type="at",
                run_at=run_at,
                timezone_name=timezone_name,
            )
        warnings = _collect_target_warnings(session_target, delivery_target)
        print(json.dumps({"ok": True, "task": _task_payload(task), "warnings": warnings}, indent=2))
        return 0
    except Exception as exc:
        _print_task_error(exc, help_command="vibe task add --help")
        return 1


def cmd_task_list(*, include_all: bool = False, brief: bool = False):
    store = _task_store()
    tasks = store.list_tasks()
    if not include_all:
        tasks = [task for task in tasks if not _is_completed_one_shot(task)]
    tasks = _sort_tasks_for_display(tasks)
    print(json.dumps({"tasks": [_task_payload(task, brief=brief) for task in tasks]}, indent=2))
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


def cmd_task_update(args):
    try:
        store = _task_store()
        task = store.get_task(args.task_id)
        if task is None:
            raise TaskCliError(
                f"task '{args.task_id}' not found",
                code="task_not_found",
                hint="Use 'vibe task list' to find a valid task ID before calling update.",
                help_command="vibe task list",
                details={"task_id": args.task_id},
            )

        if getattr(args, "reset_delivery", False) and (
            getattr(args, "post_to", None) is not None or getattr(args, "deliver_key", None) is not None
        ):
            raise TaskCliError(
                "use either --reset-delivery or a new delivery flag, not both",
                code="conflicting_delivery_target",
                hint="Pass --reset-delivery to clear delivery overrides, or pass --post-to/--deliver-key to replace them.",
                help_command="vibe task update --help",
            )
        session_key = args.session_key or task.session_key
        if getattr(args, "reset_delivery", False):
            post_to = None
            deliver_key = None
        else:
            requested_post_to = getattr(args, "post_to", None)
            requested_deliver_key = getattr(args, "deliver_key", None)
            if requested_post_to is not None:
                post_to = requested_post_to
                deliver_key = None
            elif requested_deliver_key is not None:
                post_to = None
                deliver_key = requested_deliver_key
            else:
                post_to = task.post_to
                deliver_key = task.deliver_key

        session_target, delivery_target = _validate_delivery_args(
            session_key=session_key,
            post_to=post_to,
            deliver_key=deliver_key,
            help_command="vibe task update --help",
        )

        if getattr(args, "name", None) is not None and getattr(args, "clear_name", False):
            raise TaskCliError(
                "use either --name or --clear-name, not both",
                code="conflicting_name_update",
                hint="Pass a new name with --name, or remove the stored name with --clear-name.",
                help_command="vibe task update --help",
            )
        if getattr(args, "clear_name", False):
            name = None
        elif getattr(args, "name", None) is not None:
            name = _normalize_task_name(args.name)
        else:
            name = task.name

        prompt_changed = getattr(args, "prompt", None) is not None or getattr(args, "prompt_file", None) is not None
        prompt = (
            _resolve_prompt_input(
                args,
                help_command="vibe task update --help",
                example_command=f"vibe task update {args.task_id}",
            )
            if prompt_changed
            else task.prompt
        )

        timezone_name = args.timezone or task.timezone
        try:
            timezone = ZoneInfo(timezone_name)
        except Exception as exc:
            raise TaskCliError(
                f"invalid timezone: {timezone_name}",
                code="invalid_timezone",
                hint="Use a valid IANA timezone such as UTC, Asia/Shanghai, or America/Los_Angeles.",
                example="Asia/Shanghai",
                help_command="vibe task update --help",
                details={"timezone": timezone_name},
            ) from exc

        if args.cron and args.at:
            raise TaskCliError(
                "use either --cron or --at when updating the schedule",
                code="conflicting_schedule_inputs",
                hint="Pass only one schedule update flag at a time.",
                help_command="vibe task update --help",
            )
        if args.cron:
            try:
                CronTrigger.from_crontab(args.cron, timezone=timezone)
            except ValueError as exc:
                raise TaskCliError(
                    f"invalid cron expression: {args.cron}",
                    code="invalid_cron",
                    hint="Use standard 5-field crontab format: minute hour day-of-month month day-of-week.",
                    example="0 * * * *",
                    help_command="vibe task update --help",
                    details={"cron": args.cron},
                ) from exc
            schedule_type = "cron"
            cron = args.cron
            run_at = None
        elif args.at:
            try:
                run_at = _normalize_run_at(args.at, timezone_name)
            except ValueError as exc:
                raise TaskCliError(
                    f"invalid --at timestamp: {args.at}",
                    code="invalid_run_at",
                    hint="Use ISO 8601, for example 2026-03-31T09:00:00+08:00 or 2026-03-31T09:00:00.",
                    example="2026-03-31T09:00:00+08:00",
                    help_command="vibe task update --help",
                    details={"at": args.at, "timezone": timezone_name},
                ) from exc
            schedule_type = "at"
            cron = None
            run_at = run_at
        else:
            schedule_type = task.schedule_type
            cron = task.cron
            run_at = task.run_at

        changes = {
            "name": name,
            "session_key": session_key,
            "prompt": prompt,
            "schedule_type": schedule_type,
            "post_to": post_to,
            "deliver_key": deliver_key,
            "cron": cron,
            "run_at": run_at,
            "timezone": timezone_name,
        }
        current = {
            "name": task.name,
            "session_key": task.session_key,
            "prompt": task.prompt,
            "schedule_type": task.schedule_type,
            "post_to": task.post_to,
            "deliver_key": task.deliver_key,
            "cron": task.cron,
            "run_at": task.run_at,
            "timezone": task.timezone,
        }
        if changes == current:
            raise TaskCliError(
                "no task fields were changed",
                code="no_task_changes",
                hint="Pass at least one field to update, such as --name, --cron, --prompt, --session-key, or --deliver-key.",
                help_command="vibe task update --help",
                details={"task_id": args.task_id},
            )

        updated = store.update_task(
            args.task_id,
            name=name,
            session_key=session_key,
            prompt=prompt,
            schedule_type=schedule_type,
            post_to=post_to,
            deliver_key=deliver_key,
            cron=cron,
            run_at=run_at,
            timezone_name=timezone_name,
        )
        warnings = _collect_target_warnings(session_target, delivery_target)
        print(json.dumps({"ok": True, "task": _task_payload(updated), "warnings": warnings}, indent=2))
        return 0
    except Exception as exc:
        _print_task_error(exc, help_command="vibe task update --help")
        return 1


def cmd_task_run(task_id: str):
    store = _task_store()
    task = store.get_task(task_id)
    if task is None:
        _print_task_error(
            TaskCliError(
                f"task '{task_id}' not found",
                code="task_not_found",
                hint="Use 'vibe task list' to find a valid task ID before calling run.",
                help_command="vibe task list",
                details={"task_id": task_id},
            )
        )
        return 1
    request = _task_request_store().enqueue_task_run(task.id)
    print(
        json.dumps(
            {
                "ok": True,
                "accepted": True,
                "execution_id": request.id,
                "request_type": request.request_type,
                "task_id": task.id,
            },
            indent=2,
        )
    )
    return 0


def cmd_hook_send(args):
    try:
        session_target, delivery_target = _validate_delivery_args(
            session_key=args.session_key,
            post_to=getattr(args, "post_to", None),
            deliver_key=getattr(args, "deliver_key", None),
            help_command="vibe hook send --help",
        )
        prompt = _resolve_prompt_input(
            args,
            help_command="vibe hook send --help",
            example_command="vibe hook send --session-key 'slack::channel::C123'",
        )
        request = _task_request_store().enqueue_hook_send(
            session_key=args.session_key,
            post_to=args.post_to,
            deliver_key=args.deliver_key,
            prompt=prompt,
        )
        warnings = _collect_target_warnings(session_target, delivery_target)
        print(
            json.dumps(
                {
                    "ok": True,
                    "accepted": True,
                    "execution_id": request.id,
                    "request_type": request.request_type,
                    "session_key": args.session_key,
                    "post_to": args.post_to,
                    "deliver_key": args.deliver_key,
                    "warnings": warnings,
                },
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        _print_task_error(exc, help_command="vibe hook send --help")
        return 1


def cmd_watch_add(args):
    try:
        session_target, delivery_target = _validate_delivery_args(
            session_key=args.session_key,
            post_to=getattr(args, "post_to", None),
            deliver_key=getattr(args, "deliver_key", None),
            help_command="vibe watch add --help",
        )
        command, shell_command = _resolve_watch_command(args, help_command="vibe watch add --help")

        if args.timeout < 0:
            raise TaskCliError(
                "--timeout must be >= 0",
                code="invalid_watch_timeout",
                hint="Use 0 for no per-cycle timeout, or a positive number of seconds.",
                help_command="vibe watch add --help",
                details={"timeout": args.timeout},
            )
        if args.retry_delay < 0:
            raise TaskCliError(
                "--retry-delay must be >= 0",
                code="invalid_watch_retry_delay",
                hint="Use 0 to retry immediately, or a positive number of seconds.",
                help_command="vibe watch add --help",
                details={"retry_delay": args.retry_delay},
            )
        if args.lifetime_timeout < 0:
            raise TaskCliError(
                "--lifetime-timeout must be >= 0",
                code="invalid_watch_lifetime_timeout",
                hint="Use 0 for no overall lifetime limit, or a positive number of seconds.",
                help_command="vibe watch add --help",
                details={"lifetime_timeout": args.lifetime_timeout},
            )
        if args.lifetime_timeout and not args.forever:
            raise TaskCliError(
                "--lifetime-timeout requires --forever",
                code="invalid_watch_lifetime_timeout",
                hint="Use --lifetime-timeout only on forever watches.",
                help_command="vibe watch add --help",
            )
        cwd = args.cwd
        if cwd:
            resolved = Path(cwd).expanduser().resolve()
            if not resolved.exists() or not resolved.is_dir():
                raise TaskCliError(
                    f"watch cwd does not exist: {cwd}",
                    code="invalid_watch_cwd",
                    hint="Point --cwd to an existing directory, or omit it to inherit the service working directory.",
                    help_command="vibe watch add --help",
                    details={"cwd": cwd},
                )
            cwd = str(resolved)

        _validate_watch_script_preflight(command, shell_command, cwd=cwd, help_command="vibe watch add --help")

        retry_exit_codes = sorted(set(args.retry_exit_code or [DEFAULT_RETRY_EXIT_CODE]))
        store = _watch_store()
        watch = store.add_watch(
            name=_normalize_watch_name(getattr(args, "name", None)),
            session_key=args.session_key,
            command=command,
            shell_command=shell_command,
            prefix=_normalize_task_name(getattr(args, "prefix", None)),
            cwd=cwd,
            mode="forever" if args.forever else "once",
            timeout_seconds=float(args.timeout),
            lifetime_timeout_seconds=float(args.lifetime_timeout),
            retry_exit_codes=retry_exit_codes,
            retry_delay_seconds=float(args.retry_delay),
            post_to=args.post_to,
            deliver_key=args.deliver_key,
        )
        runtime_store = _watch_runtime_store()
        watch, runtime_entry = _wait_for_watch_startup(store, runtime_store, watch.id)
        warnings = _collect_target_warnings(session_target, delivery_target)
        print(json.dumps({"ok": True, "watch": _watch_payload(watch, runtime_entry), "warnings": warnings}, indent=2))
        return 0
    except Exception as exc:
        _print_task_error(exc, help_command="vibe watch add --help")
        return 1


def cmd_watch_list(*, brief: bool = False):
    store = _watch_store()
    runtime_state = _watch_runtime_store().load().get("watches", {})
    watches = store.list_watches()
    watches.sort(key=lambda item: (item.enabled is False, item.created_at, item.id))
    print(
        json.dumps(
            {"watches": [_watch_payload(watch, runtime_state.get(watch.id), brief=brief) for watch in watches]},
            indent=2,
        )
    )
    return 0


def cmd_watch_show(watch_id: str):
    store = _watch_store()
    watch = store.get_watch(watch_id)
    if watch is None:
        _print_task_error(
            TaskCliError(
                f"watch '{watch_id}' not found",
                code="watch_not_found",
                hint="Use 'vibe watch list' to find a valid watch ID before calling show.",
                help_command="vibe watch list",
                details={"watch_id": watch_id},
            )
        )
        return 1
    runtime_entry = _watch_runtime_store().load().get("watches", {}).get(watch.id)
    print(json.dumps({"ok": True, "watch": _watch_payload(watch, runtime_entry)}, indent=2))
    return 0


def cmd_watch_set_enabled(watch_id: str, enabled: bool):
    store = _watch_store()
    watch = store.get_watch(watch_id)
    if watch is None:
        action = "resume" if enabled else "pause"
        _print_task_error(
            TaskCliError(
                f"watch '{watch_id}' not found",
                code="watch_not_found",
                hint=f"Use 'vibe watch list' to find a valid watch ID before calling {action}.",
                help_command="vibe watch list",
                details={"watch_id": watch_id},
            )
        )
        return 1
    updated = store.set_enabled(watch_id, enabled)
    runtime_entry = _watch_runtime_store().load().get("watches", {}).get(updated.id)
    print(json.dumps({"ok": True, "watch": _watch_payload(updated, runtime_entry)}, indent=2))
    return 0


def cmd_watch_remove(watch_id: str):
    store = _watch_store()
    removed = store.remove_watch(watch_id)
    if not removed:
        _print_task_error(
            TaskCliError(
                f"watch '{watch_id}' not found",
                code="watch_not_found",
                hint="Use 'vibe watch list' to find a valid watch ID before calling remove.",
                help_command="vibe watch list",
                details={"watch_id": watch_id},
            )
        )
        return 1
    print(json.dumps({"ok": True, "removed_id": watch_id}, indent=2))
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
            print("  vibe restart")
            return 0
        else:
            print(f"\033[31mUpgrade failed:\033[0m\n{result.stderr}")
            return 1
    except Exception as e:
        print(f"\033[31mUpgrade failed: {e}\033[0m")
        return 1


def cmd_restart():
    """Restart all services (stop + start)."""
    return _cmd_restart_with_delay(0.0)


def _format_restart_delay(delay_seconds: float) -> str:
    if delay_seconds == int(delay_seconds):
        whole_seconds = int(delay_seconds)
        if whole_seconds % 60 == 0:
            minutes = whole_seconds // 60
            if minutes == 1:
                return "1 minute"
            return f"{minutes} minutes"
        if whole_seconds == 1:
            return "1 second"
        return f"{whole_seconds} seconds"
    return f"{delay_seconds:g} seconds"


def _schedule_delayed_restart(delay_seconds: float) -> int:
    current_vibe_path = cache_running_vibe_path()
    restart_command = [*get_restart_command(vibe_path=current_vibe_path), "restart"]
    api._spawn_delayed_restart(
        restart_command,
        get_safe_cwd(),
        delay_seconds=delay_seconds,
        env=get_restart_environment(vibe_path=current_vibe_path),
    )
    print(f"Restart scheduled in {_format_restart_delay(delay_seconds)}.")
    print("This command exits immediately; the delayed restart will run in the background.")
    return 0


def _cmd_restart_with_delay(delay_seconds: float) -> int:
    if delay_seconds > 0:
        return _schedule_delayed_restart(delay_seconds)

    print("Restarting vibe services...")
    cmd_stop()
    print("Waiting 3 seconds...")
    time.sleep(3)
    return cmd_vibe()


def build_parser():
    parser = VibeArgumentParser(prog="vibe")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("stop", help="Stop all services")
    restart_parser = subparsers.add_parser("restart", help="Restart all services")
    restart_parser.add_argument(
        "--delay-seconds",
        type=_non_negative_float,
        default=0,
        help="Schedule the restart to run asynchronously after N seconds, then exit immediately.",
    )
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
        metavar="{add,update,list,show,pause,resume,run,remove}",
    )
    task_subparsers.required = True

    task_add_parser = task_subparsers.add_parser(
        "add",
        help="Create a scheduled task",
        description="Create a recurring or one-shot scheduled prompt.",
        epilog=_task_add_examples_text(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe task add --help",
        error_hint="Use --session-key together with exactly one schedule flag and one prompt input flag. Add --post-to or --deliver-key only when delivery must differ from the session target.",
    )
    task_add_parser.add_argument(
        "--name",
        help="Optional human-friendly task name",
    )
    task_add_parser.add_argument(
        "--session-key",
        required=True,
        help="Conversation session key to continue when the task runs.",
    )
    delivery_group = task_add_parser.add_mutually_exclusive_group()
    delivery_group.add_argument(
        "--post-to",
        choices=("thread", "channel"),
        help="Delivery location override. This changes where the message is posted, not which session is continued.",
    )
    delivery_group.add_argument(
        "--deliver-key",
        help="Explicit delivery target key. Use this only when delivery must go to a different target than the continued session.",
    )
    schedule_group = task_add_parser.add_mutually_exclusive_group(required=True)
    schedule_group.add_argument("--cron", help="Recurring schedule in 5-field crontab format")
    schedule_group.add_argument("--at", help="One-shot timestamp in ISO 8601 format")
    prompt_group = task_add_parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="Stored task content to inject each time the task runs")
    prompt_group.add_argument("--prompt-file", help="Read stored task content from a UTF-8 text file")
    task_add_parser.add_argument("--timezone", help="IANA timezone name used for --cron and naive --at values")

    task_update_parser = task_subparsers.add_parser(
        "update",
        help="Update a scheduled task",
        description="Update one stored scheduled task while keeping its task ID.",
        epilog=_task_update_examples_text(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe task update --help",
        error_hint="Pass the task ID plus at least one field to change. Unspecified fields keep their existing values.",
    )
    task_update_parser.add_argument("task_id", help="Task ID from 'vibe task list'")
    task_update_parser.add_argument("--name", help="New human-friendly task name")
    task_update_parser.add_argument(
        "--clear-name",
        action="store_true",
        help="Remove the stored custom task name",
    )
    task_update_parser.add_argument("--session-key", help="Replace the stored session key")
    update_delivery_group = task_update_parser.add_mutually_exclusive_group()
    update_delivery_group.add_argument(
        "--post-to",
        choices=("thread", "channel"),
        help="Replace the delivery location override",
    )
    update_delivery_group.add_argument(
        "--deliver-key",
        help="Replace the explicit delivery target key",
    )
    task_update_parser.add_argument(
        "--reset-delivery",
        action="store_true",
        help="Clear any stored delivery override so delivery follows --session-key directly",
    )
    task_update_parser.add_argument("--cron", help="Replace the schedule with a recurring 5-field crontab")
    task_update_parser.add_argument("--at", help="Replace the schedule with a one-shot ISO 8601 timestamp")
    task_update_parser.add_argument("--prompt", help="Replace the stored prompt text")
    task_update_parser.add_argument("--prompt-file", help="Replace the stored prompt from a UTF-8 text file")
    task_update_parser.add_argument("--timezone", help="Replace the stored IANA timezone name")

    task_subparsers.add_parser(
        "list",
        help="List scheduled tasks",
        description="List stored scheduled tasks. Completed one-shot tasks are hidden unless --all is used.",
        epilog="Use the returned task IDs with 'vibe task show', 'vibe task update', 'vibe task run', 'vibe task pause', 'vibe task resume', or 'vibe task remove'.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe task list --help",
    )
    task_list_parser = task_subparsers.choices["list"]
    task_list_parser.add_argument(
        "--all",
        action="store_true",
        help="Include completed one-shot tasks that are hidden by default",
    )
    task_list_parser.add_argument(
        "--brief",
        action="store_true",
        help="Show a compact scheduling-focused view instead of the full stored task payload",
    )
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

    task_run_parser = task_subparsers.add_parser(
        "run",
        help="Run a scheduled task immediately",
        description="Queue one immediate execution of an existing scheduled task.",
        epilog="Find task IDs with: vibe task list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe task run --help",
    )
    task_run_parser.add_argument("task_id", help="Task ID from 'vibe task list'")

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

    hook_parser = subparsers.add_parser(
        "hook",
        help="Send one-shot async hooks",
        description="Queue one-shot asynchronous turns without persisting scheduled tasks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe hook --help",
        error_hint="Run 'vibe hook send --help' for the async hook command shape.",
    )
    hook_subparsers = hook_parser.add_subparsers(dest="hook_command", metavar="{send}")
    hook_subparsers.required = True
    hook_send_parser = hook_subparsers.add_parser(
        "send",
        help="Queue one async hook message",
        description="Queue one asynchronous turn for a session key without storing a scheduled task.",
        epilog=_hook_send_examples_text(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe hook send --help",
        error_hint="Use --session-key together with exactly one prompt input flag. Add --post-to or --deliver-key only when delivery must differ from the session target.",
    )
    hook_send_parser.add_argument(
        "--session-key",
        required=True,
        help="Conversation session key to continue for this one-shot async turn.",
    )
    hook_delivery_group = hook_send_parser.add_mutually_exclusive_group()
    hook_delivery_group.add_argument(
        "--post-to",
        choices=("thread", "channel"),
        help="Delivery location override. This changes where the message is posted, not which session is continued.",
    )
    hook_delivery_group.add_argument(
        "--deliver-key",
        help="Explicit delivery target key. Use this only when delivery must go to a different target than the continued session.",
    )
    hook_prompt_group = hook_send_parser.add_mutually_exclusive_group(required=True)
    hook_prompt_group.add_argument("--prompt", help="One-shot async content to queue immediately")
    hook_prompt_group.add_argument("--prompt-file", help="Read one-shot async content from a UTF-8 text file")

    watch_parser = subparsers.add_parser(
        "watch",
        help="Manage background watches",
        description="Create, inspect, and control managed background watchers for Vibe Remote.",
        epilog=_watch_examples_text(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe watch --help",
        error_hint="Run one of the watch subcommands below. Use 'vibe watch add --help' for watch creation details.",
    )
    watch_subparsers = watch_parser.add_subparsers(
        dest="watch_command",
        metavar="{add,list,show,pause,resume,remove}",
    )
    watch_subparsers.required = True

    watch_add_parser = watch_subparsers.add_parser(
        "add",
        help="Create a managed background watch",
        description="Create a managed background watch that runs a waiter command and sends a follow-up on success or terminal failure.",
        epilog=_watch_add_examples_text(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe watch add --help",
        error_hint="Use --session-key and either --shell or a command after '--'. Add --forever only when the waiter should re-arm after successful cycles and only retry failures for explicit retry exit codes.",
    )
    watch_add_parser.add_argument("--name", help="Optional human-friendly watch name")
    watch_add_parser.add_argument(
        "--session-key",
        required=True,
        help="Conversation session key to continue for follow-up messages from this watch.",
    )
    watch_delivery_group = watch_add_parser.add_mutually_exclusive_group()
    watch_delivery_group.add_argument(
        "--post-to",
        choices=("thread", "channel"),
        help="Delivery location override. This changes where the follow-up is posted, not which session is continued.",
    )
    watch_delivery_group.add_argument(
        "--deliver-key",
        help="Explicit delivery target key. Use this only when delivery must go to a different target than the continued session.",
    )
    watch_add_parser.add_argument(
        "--prefix",
        help="Optional follow-up instruction text prepended before waiter stdout, joined with a blank line when both exist.",
    )
    watch_add_parser.add_argument("--cwd", help="Working directory for the waiter process")
    watch_add_parser.add_argument(
        "--timeout",
        type=float,
        default=21600,
        help="Per-cycle timeout in seconds. Use 0 for no per-cycle timeout. Default: 21600",
    )
    watch_add_parser.add_argument(
        "--forever",
        action="store_true",
        help="Keep re-arming the watch after each successful cycle instead of stopping after the first event. Terminal failures still stop the watch unless a retry exit code is allowed.",
    )
    watch_add_parser.add_argument(
        "--lifetime-timeout",
        type=float,
        default=0,
        help="Overall forever-watch lifetime timeout in seconds. Use 0 for no lifetime limit. Requires --forever.",
    )
    watch_add_parser.add_argument(
        "--retry-exit-code",
        dest="retry_exit_code",
        action="append",
        type=int,
        default=None,
        help=f"Cycle exit code that should be retried in forever mode. Repeat to add more. Default: {DEFAULT_RETRY_EXIT_CODE}",
    )
    watch_add_parser.add_argument(
        "--retry-delay",
        type=float,
        default=30,
        help="Delay in seconds before retrying an allowed forever cycle failure. Default: 30",
    )
    watch_add_parser.add_argument(
        "--shell",
        help="Shell command to run as the waiter. Use this or pass a command after '--'.",
    )
    watch_add_parser.add_argument(
        "waiter_command",
        nargs=argparse.REMAINDER,
        help="Waiter command to run after '--'. Example: vibe watch add ... -- python3 script.py --flag value",
    )

    watch_list_parser = watch_subparsers.add_parser(
        "list",
        help="List background watches",
        description="List stored managed background watches.",
        epilog="Use the returned watch IDs with 'vibe watch show', 'vibe watch pause', 'vibe watch resume', or 'vibe watch remove'.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe watch list --help",
    )
    watch_list_parser.add_argument(
        "--brief",
        action="store_true",
        help="Show a compact watcher-focused view instead of the full stored watch payload",
    )
    _add_hidden_task_alias(watch_subparsers, "ls", watch_list_parser)

    watch_show_parser = watch_subparsers.add_parser(
        "show",
        help="Show one background watch",
        description="Show one managed background watch by ID.",
        epilog="Find watch IDs with: vibe watch list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe watch show --help",
    )
    watch_show_parser.add_argument("watch_id", help="Watch ID from 'vibe watch list'")

    watch_pause_parser = watch_subparsers.add_parser(
        "pause",
        help="Pause one background watch",
        description="Disable one managed background watch without deleting it.",
        epilog="Find watch IDs with: vibe watch list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe watch pause --help",
    )
    watch_pause_parser.add_argument("watch_id", help="Watch ID from 'vibe watch list'")

    watch_resume_parser = watch_subparsers.add_parser(
        "resume",
        help="Resume one background watch",
        description="Re-enable one paused managed background watch.",
        epilog="Find watch IDs with: vibe watch list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe watch resume --help",
    )
    watch_resume_parser.add_argument("watch_id", help="Watch ID from 'vibe watch list'")

    watch_remove_parser = watch_subparsers.add_parser(
        "remove",
        help="Remove one background watch",
        description="Delete one managed background watch permanently.",
        epilog="Find watch IDs with: vibe watch list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        error_help_command="vibe watch remove --help",
    )
    watch_remove_parser.add_argument("watch_id", help="Watch ID from 'vibe watch list'")
    _add_hidden_task_alias(watch_subparsers, "rm", watch_remove_parser)
    return parser


def main():
    cache_running_vibe_path()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "stop":
        sys.exit(cmd_stop())
    if args.command == "restart":
        sys.exit(_cmd_restart_with_delay(args.delay_seconds))
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
        if args.task_command == "update":
            sys.exit(cmd_task_update(args))
        if args.task_command in {"list", "ls"}:
            sys.exit(cmd_task_list(include_all=getattr(args, "all", False), brief=getattr(args, "brief", False)))
        if args.task_command == "show":
            sys.exit(cmd_task_show(args.task_id))
        if args.task_command == "pause":
            sys.exit(cmd_task_set_enabled(args.task_id, False))
        if args.task_command == "resume":
            sys.exit(cmd_task_set_enabled(args.task_id, True))
        if args.task_command == "run":
            sys.exit(cmd_task_run(args.task_id))
        if args.task_command in {"remove", "rm"}:
            sys.exit(cmd_task_remove(args.task_id))
        parser.error("task command is required")
    if args.command == "hook":
        if args.hook_command == "send":
            sys.exit(cmd_hook_send(args))
        parser.error("hook command is required")
    if args.command == "watch":
        if args.watch_command == "add":
            sys.exit(cmd_watch_add(args))
        if args.watch_command in {"list", "ls"}:
            sys.exit(cmd_watch_list(brief=getattr(args, "brief", False)))
        if args.watch_command == "show":
            sys.exit(cmd_watch_show(args.watch_id))
        if args.watch_command == "pause":
            sys.exit(cmd_watch_set_enabled(args.watch_id, False))
        if args.watch_command == "resume":
            sys.exit(cmd_watch_set_enabled(args.watch_id, True))
        if args.watch_command in {"remove", "rm"}:
            sys.exit(cmd_watch_remove(args.watch_id))
        parser.error("watch command is required")
    sys.exit(cmd_vibe())
