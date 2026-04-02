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

from core.watches import ManagedWatchStore, WatchRuntimeStateStore
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


def _parse_watch_add(argv: list[str]):
    parser = cli.build_parser()
    return parser.parse_args(["watch", "add", *argv])


def _capture_stderr_json(func, *args):
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        result = func(*args)
    return result, json.loads(stderr.getvalue())


def test_watch_help_describes_session_key_guidance(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["watch", "--help"])

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "managed background watchers" in captured.out
    assert "vibe watch add --session-key 'slack::channel::C123'" in captured.out
    assert "{add,list,show,pause,resume,remove}" in captured.out


def test_watch_add_help_mentions_shell_and_lifetime_timeout(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["watch", "add", "--help"])

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Pass either --shell '<command>' or a command after '--'." in captured.out
    assert "--lifetime-timeout" in captured.out
    assert "vibe watch add --session-key 'slack::channel::C123'" in captured.out


def test_watch_add_parser_keeps_top_level_command_name() -> None:
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--",
            "python3",
            "wait.py",
        ]
    )

    assert args.command == "watch"
    assert args.watch_command == "add"
    assert args.waiter_command == ["--", "python3", "wait.py"]


def test_watch_add_missing_command_is_structured_json() -> None:
    args = _parse_watch_add(["--session-key", "slack::channel::C123"])

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_watch_add, args)

    assert result == 1
    assert payload["code"] == "missing_watch_command"
    assert payload["help_command"] == "vibe watch add --help"


def test_watch_add_rejects_lifetime_timeout_without_forever() -> None:
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--lifetime-timeout",
            "10",
            "--shell",
            "echo done",
        ]
    )

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_watch_add, args)

    assert result == 1
    assert payload["code"] == "invalid_watch_lifetime_timeout"


def test_watch_add_rejects_missing_cwd() -> None:
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--cwd",
            "/tmp/definitely-missing-watch-dir",
            "--shell",
            "echo done",
        ]
    )

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_watch_add, args)

    assert result == 1
    assert payload["code"] == "invalid_watch_cwd"


def test_watch_add_creates_shell_watch(tmp_path: Path, capsys) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--name",
            "Wait for export",
            "--prefix",
            "Export finished.",
            "--shell",
            "python3 scripts/wait.py",
        ]
    )

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
    ):
        result = cli.cmd_watch_add(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["watch"]["name"] == "Wait for export"
    assert payload["watch"]["shell_command"] == "python3 scripts/wait.py"
    assert payload["watch"]["command"] == []
    assert payload["watch"]["mode"] == "once"


def test_watch_add_creates_exec_watch_with_retry_codes(tmp_path: Path, capsys) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--forever",
            "--timeout",
            "600",
            "--lifetime-timeout",
            "7200",
            "--retry-exit-code",
            "1",
            "--retry-exit-code",
            "75",
            "--",
            "python3",
            "scripts/wait.py",
            "--build",
            "42",
        ]
    )

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
    ):
        result = cli.cmd_watch_add(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["watch"]["mode"] == "forever"
    assert payload["watch"]["command"] == ["python3", "scripts/wait.py", "--build", "42"]
    assert payload["watch"]["retry_exit_codes"] == [1, 75]


def test_watch_add_persists_absolute_cwd(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    workdir = tmp_path / "repo"
    workdir.mkdir()
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--cwd",
            str(workdir.relative_to(tmp_path)),
            "--shell",
            "echo done",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
    ):
        result = cli.cmd_watch_add(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["watch"]["cwd"] == str(workdir.resolve())


def test_watch_list_brief_includes_runtime_state(tmp_path: Path, capsys) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    watch = store.add_watch(
        name="Watch CI",
        session_key="slack::channel::C123",
        command=["python3", "wait.py"],
        shell_command=None,
        prefix=None,
        cwd=None,
        mode="forever",
        timeout_seconds=600,
        lifetime_timeout_seconds=0,
        retry_exit_codes=[1],
        retry_delay_seconds=30,
        post_to=None,
        deliver_key=None,
    )
    runtime_store.write(
        {
            "watches": {
                watch.id: {
                    "running": True,
                    "pid": 1234,
                    "started_at": "2026-04-02T00:00:00+00:00",
                    "updated_at": "2026-04-02T00:00:00+00:00",
                }
            }
        }
    )

    with (
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
    ):
        result = cli.cmd_watch_list(brief=True)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["watches"][0]["state"] == "running"
    assert payload["watches"][0]["mode"] == "forever"


def test_watch_show_missing_returns_structured_error() -> None:
    result, payload = _capture_stderr_json(cli.cmd_watch_show, "missing-watch")

    assert result == 1
    assert payload["code"] == "watch_not_found"


def test_watch_pause_resume_and_remove_update_store(tmp_path: Path, capsys) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    watch = store.add_watch(
        name="Watch CI",
        session_key="slack::channel::C123",
        command=["python3", "wait.py"],
        shell_command=None,
        prefix=None,
        cwd=None,
        mode="once",
        timeout_seconds=600,
        lifetime_timeout_seconds=0,
        retry_exit_codes=[1],
        retry_delay_seconds=30,
        post_to=None,
        deliver_key=None,
    )

    with (
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
    ):
        assert cli.cmd_watch_set_enabled(watch.id, False) == 0
        paused = json.loads(capsys.readouterr().out)
        assert paused["watch"]["enabled"] is False

        assert cli.cmd_watch_set_enabled(watch.id, True) == 0
        resumed = json.loads(capsys.readouterr().out)
        assert resumed["watch"]["enabled"] is True

        assert cli.cmd_watch_remove(watch.id) == 0
        removed = json.loads(capsys.readouterr().out)
        assert removed["removed_id"] == watch.id
