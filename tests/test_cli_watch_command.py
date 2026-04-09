from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
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


def _startup_ok(store: ManagedWatchStore, runtime_store: WatchRuntimeStateStore, watch_id: str):
    return store.get_watch(watch_id), runtime_store.load().get("watches", {}).get(watch_id)


def _write_script(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("print('ok')\n")


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
    assert "`--prefix` becomes the instruction text of the follow-up hook." in captured.out
    assert "Terminal failures also send a follow-up and disable the watch." in captured.out
    assert "If this is your first time using this command, read this whole help entry before creating a watch." in captured.out


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


def test_watch_add_rejects_missing_relative_script_from_current_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--",
            "uv",
            "run",
            "--no-project",
            "scripts/wait_pr.py",
            "--repo",
            "cyhhao/vibe-remote",
            "--pr",
            "178",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_watch_add, args)

    assert result == 1
    assert payload["code"] == "invalid_watch_script"
    assert payload["details"]["script"] == "scripts/wait_pr.py"
    assert payload["details"]["resolved_path"] == str((tmp_path / "scripts" / "wait_pr.py").resolve())


def test_watch_add_rejects_missing_relative_script_from_explicit_cwd(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--cwd",
            str(repo_root),
            "--shell",
            "python3 scripts/wait.py",
        ]
    )

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_watch_add, args)

    assert result == 1
    assert payload["code"] == "invalid_watch_script"
    assert payload["details"]["resolved_path"] == str((repo_root / "scripts" / "wait.py").resolve())
    assert payload["details"]["checked_from"] == str(repo_root.resolve())


def test_watch_add_accepts_existing_relative_script_from_current_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    script_path = tmp_path / "scripts" / "wait.py"
    script_path.parent.mkdir()
    script_path.write_text("print('ok')\n")
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--",
            "python3",
            "scripts/wait.py",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
        patch("vibe.cli._wait_for_watch_startup", side_effect=lambda *args, **kwargs: _startup_ok(store, runtime_store, args[2])),
    ):
        result = cli.cmd_watch_add(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["watch"]["command"] == ["python3", "scripts/wait.py"]


def test_watch_add_skips_uv_option_values_before_script_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _write_script(tmp_path / "scripts" / "wait.py")
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--",
            "uv",
            "run",
            "--project",
            str(project_dir),
            "scripts/wait.py",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
        patch("vibe.cli._wait_for_watch_startup", side_effect=lambda *args, **kwargs: _startup_ok(store, runtime_store, args[2])),
    ):
        result = cli.cmd_watch_add(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["watch"]["command"] == ["uv", "run", "--project", str(project_dir), "scripts/wait.py"]


def test_watch_add_preflights_script_after_valueless_uv_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--",
            "uv",
            "run",
            "-n",
            "scripts/wait.py",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_watch_add, args)

    assert result == 1
    assert payload["code"] == "invalid_watch_script"
    assert payload["details"]["script"] == "scripts/wait.py"
    assert payload["details"]["resolved_path"] == str((tmp_path / "scripts" / "wait.py").resolve())


def test_watch_add_preflights_script_after_uv_refresh_package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--",
            "uv",
            "run",
            "--refresh-package",
            "foo",
            "scripts/wait.py",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_watch_add, args)

    assert result == 1
    assert payload["code"] == "invalid_watch_script"
    assert payload["details"]["script"] == "scripts/wait.py"


def test_watch_add_preflights_script_after_python_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--",
            "python3",
            "-u",
            "scripts/wait.py",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_watch_add, args)

    assert result == 1
    assert payload["code"] == "invalid_watch_script"
    assert payload["details"]["script"] == "scripts/wait.py"


def test_watch_add_preflights_script_after_shell_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--",
            "bash",
            "-e",
            "scripts/wait.sh",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_watch_add, args)

    assert result == 1
    assert payload["code"] == "invalid_watch_script"
    assert payload["details"]["script"] == "scripts/wait.sh"


def test_watch_add_does_not_treat_shell_command_string_as_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--",
            "bash",
            "-lc",
            "sleep 1; echo done",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
        patch("vibe.cli._wait_for_watch_startup", side_effect=lambda *args, **kwargs: _startup_ok(store, runtime_store, args[2])),
    ):
        result = cli.cmd_watch_add(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["watch"]["command"] == ["bash", "-lc", "sleep 1; echo done"]


def test_watch_add_accepts_shell_command_with_home_expansion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    home_dir = tmp_path / "home"
    _write_script(home_dir / "scripts" / "wait.py")
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--shell",
            "python3 $HOME/scripts/wait.py",
        ]
    )

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.chdir(tmp_path)

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
        patch("vibe.cli._wait_for_watch_startup", side_effect=lambda *args, **kwargs: _startup_ok(store, runtime_store, args[2])),
    ):
        result = cli.cmd_watch_add(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["watch"]["shell_command"] == "python3 $HOME/scripts/wait.py"


def test_watch_add_rejects_exec_command_with_literal_tilde_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    _write_script(home_dir / "scripts" / "wait.py")
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--",
            "python3",
            "~/scripts/wait.py",
        ]
    )

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.chdir(tmp_path)

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_watch_add, args)

    assert result == 1
    assert payload["code"] == "invalid_watch_script"
    assert payload["details"]["script"] == "~/scripts/wait.py"


def test_watch_add_accepts_shell_command_with_glob_expansion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    _write_script(tmp_path / "scripts" / "wait.py")
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--shell",
            "python3 scripts/*.py",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
        patch("vibe.cli._wait_for_watch_startup", side_effect=lambda *args, **kwargs: _startup_ok(store, runtime_store, args[2])),
    ):
        result = cli.cmd_watch_add(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["watch"]["shell_command"] == "python3 scripts/*.py"


def test_watch_add_preflights_versioned_python_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--",
            "python3.11",
            "scripts/wait.py",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})):
        result, payload = _capture_stderr_json(cli.cmd_watch_add, args)

    assert result == 1
    assert payload["code"] == "invalid_watch_script"
    assert payload["details"]["script"] == "scripts/wait.py"


def test_watch_add_resolves_script_from_uv_directory_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    repo_root = tmp_path / "repo"
    _write_script(repo_root / "scripts" / "wait.py")
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--",
            "uv",
            "run",
            "--directory",
            str(repo_root),
            "scripts/wait.py",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
        patch("vibe.cli._wait_for_watch_startup", side_effect=lambda *args, **kwargs: _startup_ok(store, runtime_store, args[2])),
    ):
        result = cli.cmd_watch_add(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["watch"]["command"] == ["uv", "run", "--directory", str(repo_root), "scripts/wait.py"]


def test_watch_add_creates_shell_watch(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    _write_script(tmp_path / "scripts" / "wait.py")
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

    monkeypatch.chdir(tmp_path)

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
        patch("vibe.cli._wait_for_watch_startup", side_effect=lambda *args, **kwargs: _startup_ok(store, runtime_store, args[2])),
    ):
        result = cli.cmd_watch_add(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["watch"]["name"] == "Wait for export"
    assert payload["watch"]["shell_command"] == "python3 scripts/wait.py"
    assert payload["watch"]["command"] == []
    assert payload["watch"]["mode"] == "once"
    assert payload["watch"]["retry_exit_codes"] == [75]


def test_watch_add_creates_exec_watch_with_retry_codes(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    _write_script(tmp_path / "scripts" / "wait.py")
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

    monkeypatch.chdir(tmp_path)

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
        patch("vibe.cli._wait_for_watch_startup", side_effect=lambda *args, **kwargs: _startup_ok(store, runtime_store, args[2])),
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
        patch("vibe.cli._wait_for_watch_startup", side_effect=lambda *args, **kwargs: _startup_ok(store, runtime_store, args[2])),
    ):
        result = cli.cmd_watch_add(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["watch"]["cwd"] == str(workdir.resolve())


def test_watch_add_returns_structured_error_when_startup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    _write_script(tmp_path / "scripts" / "wait.py")
    args = _parse_watch_add(
        [
            "--session-key",
            "slack::channel::C123",
            "--shell",
            "python3 scripts/wait.py",
        ]
    )

    monkeypatch.chdir(tmp_path)

    with (
        patch("vibe.cli._ensure_config", return_value=_configured_v2({"slack"})),
        patch("vibe.cli._watch_store", return_value=store),
        patch("vibe.cli._watch_runtime_store", return_value=runtime_store),
        patch(
            "vibe.cli._wait_for_watch_startup",
            side_effect=cli.TaskCliError(
                "watch failed during startup and has already been disabled",
                code="watch_startup_failed",
                hint="Inspect the stored watch error, fix the waiter or its dependencies, then recreate the watch if monitoring should continue.",
                example="vibe watch show abc",
                help_command="vibe watch show abc",
            ),
        ),
    ):
        result, payload = _capture_stderr_json(cli.cmd_watch_add, args)

    assert result == 1
    assert payload["code"] == "watch_startup_failed"
    assert payload["hint"].startswith("Inspect the stored watch error")
    assert payload["example"] == "vibe watch show abc"
    assert payload["help_command"] == "vibe watch show abc"


def test_wait_for_watch_startup_accepts_stably_running_watch(tmp_path: Path) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    watch = store.add_watch(
        name="Stable watch",
        session_key="slack::channel::C123",
        command=["python3", "wait.py"],
        shell_command=None,
        prefix=None,
        cwd=None,
        mode="forever",
        timeout_seconds=600,
        lifetime_timeout_seconds=0,
        retry_exit_codes=[75],
        retry_delay_seconds=30,
        post_to=None,
        deliver_key=None,
    )
    watch.last_started_at = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    store.upsert_watch(watch)
    runtime_store.write(
        {
            "watches": {
                watch.id: {
                    "running": True,
                    "pid": 1234,
                    "started_at": (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            }
        }
    )

    resolved_watch, runtime_entry = cli._wait_for_watch_startup(
        store,
        runtime_store,
        watch.id,
        timeout_seconds=0.2,
        poll_interval_seconds=0.01,
        stable_running_seconds=1.5,
    )

    assert resolved_watch.id == watch.id
    assert runtime_entry["running"] is True


def test_default_watch_startup_timeout_exceeds_reconcile_and_stable_windows() -> None:
    timeout_seconds = cli._default_watch_startup_timeout_seconds(
        stable_running_seconds=cli.WATCH_STARTUP_STABLE_RUNNING_SECONDS
    )

    assert timeout_seconds > cli.WATCH_RECONCILE_INTERVAL_SECONDS + cli.WATCH_STARTUP_STABLE_RUNNING_SECONDS


def test_wait_for_watch_startup_rejects_watch_that_fails_before_stable_window(tmp_path: Path) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    watch = store.add_watch(
        name="Flaky watch",
        session_key="slack::channel::C123",
        command=["python3", "wait.py"],
        shell_command=None,
        prefix=None,
        cwd=None,
        mode="forever",
        timeout_seconds=600,
        lifetime_timeout_seconds=0,
        retry_exit_codes=[75],
        retry_delay_seconds=30,
        post_to=None,
        deliver_key=None,
    )
    watch.last_started_at = datetime.now(timezone.utc).isoformat()
    store.upsert_watch(watch)
    runtime_store.write(
        {
            "watches": {
                watch.id: {
                    "running": True,
                    "pid": 1234,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            }
        }
    )

    monotonic_values = iter([0.0, 0.05, 0.1, 0.15, 0.2])

    def _fail_watch(_seconds: float) -> None:
        failed = store.get_watch(watch.id)
        assert failed is not None
        failed.enabled = False
        failed.last_error = "waiter crashed"
        failed.last_exit_code = 1
        store.upsert_watch(failed)
        runtime_store.write({"watches": {}})

    with (
        patch("vibe.cli.time.monotonic", side_effect=lambda: next(monotonic_values)),
        patch("vibe.cli.time.sleep", side_effect=_fail_watch),
    ):
        with pytest.raises(cli.TaskCliError) as exc:
            cli._wait_for_watch_startup(
                store,
                runtime_store,
                watch.id,
                timeout_seconds=0.2,
                poll_interval_seconds=0.01,
                stable_running_seconds=1.5,
            )

    assert exc.value.code == "watch_startup_failed"


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
        retry_exit_codes=[75],
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
        retry_exit_codes=[75],
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
