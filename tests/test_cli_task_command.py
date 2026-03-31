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


def _parse_hook_send(argv: list[str]):
    parser = cli.build_parser()
    return parser.parse_args(["hook", "send", *argv])


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
    assert "{add,update,list,show,pause,resume,run,remove}" in captured.out
    assert "rm (remove)" not in captured.out
    assert "\n    ls" not in captured.out


def test_task_add_help_includes_examples_and_threadless_guidance(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["task", "add", "--help"])

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Prefer a threadless session key by default." in captured.out
    assert "--post-to" in captured.out
    assert "--deliver-key" in captured.out
    assert "<platform>::channel::<channel_id>" in captured.out
    assert "vibe task add --session-key 'slack::channel::C123'" in captured.out


def test_task_list_help_mentions_completed_one_shots_hidden_by_default(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["task", "list", "--help"])

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Completed one-shot tasks are hidden unless --all is used." in captured.out
    assert "--all" in captured.out
    assert "--brief" in captured.out


def test_task_update_help_includes_partial_update_guidance(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["task", "update", "--help"])

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "keeping its task ID" in captured.out
    assert "--reset-delivery" in captured.out
    assert "Unspecified fields keep their existing values." in captured.out


def test_hook_send_help_includes_examples_and_threadless_guidance(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["hook", "send", "--help"])

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "`vibe hook send` queues one asynchronous turn" in captured.out
    assert "--post-to" in captured.out
    assert "--deliver-key" in captured.out
    assert "<platform>::channel::<channel_id>" in captured.out
    assert "vibe hook send --session-key 'slack::channel::C123'" in captured.out


def test_task_add_parse_error_is_structured_json(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["task", "add", "--session-key", "slack::channel::C123"])

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == "invalid_arguments"
    assert payload["help_command"] == "vibe task add --help"
    assert "--session-key SESSION_KEY" in payload["usage"]


def test_task_remove_alias_parse_error_keeps_structured_guidance(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["task", "rm"])

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == "invalid_arguments"
    assert payload["help_command"] == "vibe task remove --help"
    assert "task_id" in payload["error"]


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


def test_task_add_rejects_conflicting_delivery_target_flags(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(
            [
                "task",
                "add",
                "--session-key",
                "slack::channel::C123",
                "--post-to",
                "channel",
                "--deliver-key",
                "slack::channel::C999",
                "--cron",
                "0 * * * *",
                "--prompt",
                "hello",
            ]
        )

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == "invalid_arguments"
    assert "not allowed with argument --post-to" in payload["error"]
    assert payload["help_command"] == "vibe task add --help"


def test_task_add_rejects_post_to_thread_without_thread_session_key() -> None:
    args = _parse_task_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--post-to",
            "thread",
            "--cron",
            "0 * * * *",
            "--prompt",
            "hello",
        ]
    )

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_task_add, args)

    assert result == 1
    assert payload["code"] == "invalid_delivery_target"


def test_task_add_rejects_cross_platform_deliver_key() -> None:
    args = _parse_task_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--deliver-key",
            "discord::channel::C999",
            "--cron",
            "0 * * * *",
            "--prompt",
            "hello",
        ]
    )

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack", "discord"})):
        result, payload = _capture_stderr_json(cli.cmd_task_add, args)

    assert result == 1
    assert payload["code"] == "invalid_delivery_target"
    assert payload["details"] == {
        "session_platform": "slack",
        "delivery_platform": "discord",
    }


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


def test_task_update_missing_id_returns_guidance(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduled_tasks.json"

    parser = cli.build_parser()
    args = parser.parse_args(["task", "update", "missing", "--name", "Updated"])

    with patch("vibe.cli._task_store", return_value=cli.ScheduledTaskStore(store_path)):
        result, payload = _capture_stderr_json(cli.cmd_task_update, args)

    assert result == 1
    assert payload["code"] == "task_not_found"
    assert payload["help_command"] == "vibe task list"


def test_task_run_missing_id_returns_guidance(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduled_tasks.json"

    with patch("vibe.cli._task_store", return_value=cli.ScheduledTaskStore(store_path)):
        result, payload = _capture_stderr_json(cli.cmd_task_run, "missing")

    assert result == 1
    assert payload["code"] == "task_not_found"
    assert payload["help_command"] == "vibe task list"


def test_task_list_hides_completed_one_shots_by_default(tmp_path: Path, capsys) -> None:
    store_path = tmp_path / "scheduled_tasks.json"
    store = cli.ScheduledTaskStore(store_path)
    store.add_task(
        session_key="slack::channel::C123",
        prompt="recurring",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )
    done = store.add_task(
        session_key="slack::channel::C123",
        prompt="one-shot",
        schedule_type="at",
        run_at="2026-03-31T09:00:00+08:00",
        timezone_name="Asia/Shanghai",
    )
    store.mark_task_result(done.id, error=None)

    with patch("vibe.cli._task_store", return_value=store):
        result = cli.cmd_task_list()

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    ids = [item["id"] for item in payload["tasks"]]
    assert done.id not in ids


def test_task_list_brief_returns_scheduling_focused_view(tmp_path: Path, capsys) -> None:
    store_path = tmp_path / "scheduled_tasks.json"
    store = cli.ScheduledTaskStore(store_path)
    task = store.add_task(
        name="Hourly summary",
        session_key="slack::channel::C123",
        prompt="recurring summary prompt",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )

    with patch("vibe.cli._task_store", return_value=store):
        result = cli.cmd_task_list(brief=True)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    entry = payload["tasks"][0]
    assert entry["id"] == task.id
    assert entry["display_name"] == "Hourly summary"
    assert "prompt" not in entry
    assert entry["next_run_at"] is not None
    assert entry["state"] == "active"


def test_task_show_includes_derived_schedule_fields(tmp_path: Path, capsys) -> None:
    store_path = tmp_path / "scheduled_tasks.json"
    store = cli.ScheduledTaskStore(store_path)
    task = store.add_task(
        name="Hourly summary",
        session_key="slack::channel::C123",
        prompt="recurring summary prompt",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )

    with patch("vibe.cli._task_store", return_value=store):
        result = cli.cmd_task_show(task.id)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task"]["display_name"] == "Hourly summary"
    assert payload["task"]["prompt_preview"] == "recurring summary prompt"
    assert payload["task"]["next_run_at"] is not None
    assert payload["task"]["state"] == "active"
    assert payload["task"]["last_status"] == "never_run"


def test_task_list_all_includes_completed_one_shots(tmp_path: Path, capsys) -> None:
    store_path = tmp_path / "scheduled_tasks.json"
    store = cli.ScheduledTaskStore(store_path)
    done = store.add_task(
        session_key="slack::channel::C123",
        prompt="one-shot",
        schedule_type="at",
        run_at="2026-03-31T09:00:00+08:00",
        timezone_name="Asia/Shanghai",
    )
    store.mark_task_result(done.id, error=None)

    with patch("vibe.cli._task_store", return_value=store):
        result = cli.cmd_task_list(include_all=True)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    ids = [item["id"] for item in payload["tasks"]]
    assert done.id in ids


def test_task_run_enqueues_request(tmp_path: Path, capsys) -> None:
    store_path = tmp_path / "scheduled_tasks.json"
    request_root = tmp_path / "task_requests"
    store = cli.ScheduledTaskStore(store_path)
    task = store.add_task(
        session_key="slack::channel::C123",
        prompt="hello",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )

    with (
        patch("vibe.cli._task_store", return_value=store),
        patch("vibe.cli._task_request_store", return_value=cli.TaskExecutionStore(request_root)),
    ):
        result = cli.cmd_task_run(task.id)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["task_id"] == task.id
    assert (request_root / "pending" / f"{payload['execution_id']}.json").exists()


def test_task_update_requires_at_least_one_change(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduled_tasks.json"
    store = cli.ScheduledTaskStore(store_path)
    task = store.add_task(
        session_key="slack::channel::C123",
        prompt="hello",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )
    parser = cli.build_parser()
    args = parser.parse_args(["task", "update", task.id])

    with patch("vibe.cli._task_store", return_value=store):
        result, payload = _capture_stderr_json(cli.cmd_task_update, args)

    assert result == 1
    assert payload["code"] == "no_task_changes"


def test_task_update_modifies_existing_task_without_changing_id(tmp_path: Path, capsys) -> None:
    store_path = tmp_path / "scheduled_tasks.json"
    store = cli.ScheduledTaskStore(store_path)
    task = store.add_task(
        session_key="slack::channel::C123",
        prompt="hello",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )
    parser = cli.build_parser()
    args = parser.parse_args(
        ["task", "update", task.id, "--name", "Morning summary", "--cron", "*/30 * * * *", "--prompt", "updated"]
    )

    with patch("vibe.cli._task_store", return_value=store):
        result = cli.cmd_task_update(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task"]["id"] == task.id
    assert payload["task"]["name"] == "Morning summary"
    assert payload["task"]["cron"] == "*/30 * * * *"
    assert payload["task"]["prompt"] == "updated"


def test_task_add_returns_reachability_warning_for_unbound_lark_dm(tmp_path: Path, capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        ["task", "add", "--session-key", "lark::user::ou_123", "--cron", "0 * * * *", "--prompt", "hello"]
    )
    fake_store = SimpleNamespace(get_user=lambda *args, **kwargs: None)

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"lark"})),
        patch("vibe.cli._task_store", return_value=cli.ScheduledTaskStore(tmp_path / "scheduled_tasks.json")),
        patch("vibe.cli.SettingsStore.get_instance", return_value=fake_store),
    ):
        result = cli.cmd_task_add(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["warnings"][0]["code"] == "lark_user_not_bound"


def test_hook_send_rejects_invalid_session_key_with_hint() -> None:
    args = _parse_hook_send(["--session-key", "slack::thread::123", "--prompt", "hello"])

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_hook_send, args)

    assert result == 1
    assert payload["code"] == "invalid_session_key"
    assert payload["help_command"] == "vibe hook send --help"


def test_hook_send_rejects_conflicting_delivery_target_flags(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(
            [
                "hook",
                "send",
                "--session-key",
                "slack::channel::C123",
                "--post-to",
                "channel",
                "--deliver-key",
                "slack::channel::C999",
                "--prompt",
                "hello",
            ]
        )

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == "invalid_arguments"
    assert "not allowed with argument --post-to" in payload["error"]
    assert payload["help_command"] == "vibe hook send --help"


def test_hook_send_rejects_cross_platform_deliver_key() -> None:
    args = _parse_hook_send(
        [
            "--session-key",
            "slack::channel::C123",
            "--deliver-key",
            "discord::channel::C999",
            "--prompt",
            "hello",
        ]
    )

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack", "discord"})):
        result, payload = _capture_stderr_json(cli.cmd_hook_send, args)

    assert result == 1
    assert payload["code"] == "invalid_delivery_target"
    assert payload["details"] == {
        "session_platform": "slack",
        "delivery_platform": "discord",
    }


def test_hook_send_enqueues_request(tmp_path: Path, capsys) -> None:
    args = _parse_hook_send(
        [
            "--session-key",
            "slack::channel::C123::thread::171717.123",
            "--post-to",
            "channel",
            "--prompt",
            "hello",
        ]
    )
    request_root = tmp_path / "task_requests"

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._task_request_store", return_value=cli.TaskExecutionStore(request_root)),
    ):
        result = cli.cmd_hook_send(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["session_key"] == "slack::channel::C123::thread::171717.123"
    assert payload["post_to"] == "channel"
    assert (request_root / "pending" / f"{payload['execution_id']}.json").exists()


def test_hook_send_returns_reachability_warning_for_unbound_lark_dm(tmp_path: Path, capsys) -> None:
    args = _parse_hook_send(
        [
            "--session-key",
            "lark::user::ou_123",
            "--prompt",
            "hello",
        ]
    )
    request_root = tmp_path / "task_requests"
    fake_store = SimpleNamespace(get_user=lambda *args, **kwargs: None)

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"lark"})),
        patch("vibe.cli._task_request_store", return_value=cli.TaskExecutionStore(request_root)),
        patch("vibe.cli.SettingsStore.get_instance", return_value=fake_store),
    ):
        result = cli.cmd_hook_send(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["warnings"][0]["code"] == "lark_user_not_bound"


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
