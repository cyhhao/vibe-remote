from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibe import cli


def _configured_v2(platforms: set[str]):
    return SimpleNamespace(
        slack=SimpleNamespace(
            bot_token="x" if "slack" in platforms else "",
            app_token="y" if "slack" in platforms else "",
        ),
        discord=SimpleNamespace(bot_token="x" if "discord" in platforms else ""),
        lark=SimpleNamespace(
            app_id="x" if "lark" in platforms else "",
            app_secret="y" if "lark" in platforms else "",
        ),
        wechat=SimpleNamespace(enable="wechat" in platforms),
        enabled_platforms=lambda: list(platforms),
    )


def _parse_task_add(argv: list[str]):
    parser = cli.build_parser()
    return parser.parse_args(["task", "add", *argv])


def _capture_stderr_json(func, *args):
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        result = func(*args)
    return result, json.loads(stderr.getvalue())


def test_task_add_rejects_unsupported_platform() -> None:
    args = _parse_task_add(
        [
            "--session-key",
            "foo::channel::C123",
            "--cron",
            "0 * * * *",
            "--prompt",
            "hello",
        ]
    )

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack", "discord"})):
        result, payload = _capture_stderr_json(cli.cmd_task_add, args)

    assert result == 1
    assert payload["code"] == "unsupported_platform"
    assert payload["details"]["requested_platform"] == "foo"
    assert payload["help_command"] == "vibe task add --help"


def test_task_add_rejects_disabled_platform_even_with_credentials_present() -> None:
    args = _parse_task_add(
        [
            "--session-key",
            "discord::channel::C123",
            "--cron",
            "0 * * * *",
            "--prompt",
            "hello",
        ]
    )

    config = _configured_v2({"slack"})
    config.discord.bot_token = "configured-but-disabled"

    with patch("vibe.cli._ensure_config", return_value=config):
        result, payload = _capture_stderr_json(cli.cmd_task_add, args)

    assert result == 1
    assert payload["code"] == "unsupported_platform"
    assert payload["details"]["configured_platforms"] == ["slack"]


def test_task_help_describes_session_key_guidance(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["task", "--help"])

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Create, inspect, and control scheduled prompts for Vibe Remote." in captured.out
    assert "vibe task add --session-key 'slack::channel::C123'" in captured.out
    assert "{add,list,show,pause,resume,remove}" in captured.out
    assert "rm (remove)" not in captured.out
    assert "\n    ls" not in captured.out


def test_task_add_help_includes_examples_and_threadless_guidance(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["task", "add", "--help"])

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Prefer a threadless session key by default." in captured.out
    assert "<platform>::channel::<channel_id>" in captured.out
    assert "vibe task add --session-key 'slack::channel::C123'" in captured.out


def test_task_add_parse_error_is_structured_json(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["task", "add", "--session-key", "slack::channel::C123"])

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == "invalid_arguments"
    assert payload["help_command"] == "vibe task add --help"
    assert "--session-key SESSION_KEY" in payload["usage"]


def test_task_add_rejects_invalid_session_key_with_hint() -> None:
    args = _parse_task_add(
        [
            "--session-key",
            "slack::thread::123",
            "--cron",
            "0 * * * *",
            "--prompt",
            "hello",
        ]
    )

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_task_add, args)

    assert result == 1
    assert payload["code"] == "invalid_session_key"
    assert payload["example"] == "slack::channel::C123"


def test_task_add_rejects_invalid_cron_with_example() -> None:
    args = _parse_task_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--cron",
            "bad cron",
            "--prompt",
            "hello",
        ]
    )

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_task_add, args)

    assert result == 1
    assert payload["code"] == "invalid_cron"
    assert payload["example"] == "0 * * * *"


def test_task_add_rejects_invalid_timezone() -> None:
    args = _parse_task_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--cron",
            "0 * * * *",
            "--prompt",
            "hello",
            "--timezone",
            "Mars/Base",
        ]
    )

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_task_add, args)

    assert result == 1
    assert payload["code"] == "invalid_timezone"
    assert payload["details"]["timezone"] == "Mars/Base"


def test_task_show_missing_id_returns_guidance(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduled_tasks.json"

    with patch("vibe.cli._task_store", return_value=cli.ScheduledTaskStore(store_path)):
        result, payload = _capture_stderr_json(cli.cmd_task_show, "missing")

    assert result == 1
    assert payload["code"] == "task_not_found"
    assert payload["help_command"] == "vibe task list"


def test_task_remove_alias_parses_to_remove_command() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["task", "remove", "task-123"])

    assert args.command == "task"
    assert args.task_command == "remove"
    assert args.task_id == "task-123"


def test_task_hidden_aliases_still_parse() -> None:
    parser = cli.build_parser()
    list_args = parser.parse_args(["task", "ls"])
    remove_args = parser.parse_args(["task", "rm", "task-123"])

    assert list_args.task_command == "ls"
    assert remove_args.task_command == "rm"
