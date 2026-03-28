from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibe import api, cli
from vibe.upgrade import (
    UpgradePlan,
    build_upgrade_plan,
    get_current_vibe_bin_dir,
    get_latest_version_info,
    get_restart_command,
    get_running_vibe_path,
    get_safe_cwd,
)


def test_build_upgrade_plan_uses_uv_and_preserves_tool_bin_dir(monkeypatch):
    monkeypatch.setattr("vibe.upgrade.os.path.exists", lambda path: True)
    monkeypatch.setattr("vibe.upgrade.os.access", lambda path, mode: True)

    plan = build_upgrade_plan(
        python_executable="/tmp/.local/share/uv/tools/vibe-remote/bin/python",
        uv_path="/usr/local/bin/uv",
        vibe_path="/custom/bin/vibe",
        base_env={"PATH": "/usr/bin"},
    )

    assert plan.method == "uv"
    assert plan.command == ["/usr/local/bin/uv", "tool", "install", "vibe-remote", "--upgrade"]
    assert plan.env is not None
    assert plan.env["UV_TOOL_BIN_DIR"] == "/custom/bin"
    assert plan.env["PATH"] == "/usr/bin"


def test_build_upgrade_plan_uses_pip_for_non_uv_install():
    plan = build_upgrade_plan(
        python_executable="/usr/bin/python3",
        uv_path="/usr/local/bin/uv",
        vibe_path="/custom/bin/vibe",
        base_env={"PATH": "/usr/bin"},
    )

    assert plan.method == "pip"
    assert plan.command == ["/usr/bin/python3", "-m", "pip", "install", "--upgrade", "vibe-remote"]
    assert plan.env == {"PATH": "/usr/bin"}


def test_build_upgrade_plan_uses_env_package_spec(monkeypatch):
    monkeypatch.setenv("VIBE_UPGRADE_PACKAGE_SPEC", "/tmp/vibe_remote-9999.0.0-py3-none-any.whl")
    monkeypatch.setattr("vibe.upgrade.os.path.exists", lambda path: True)
    monkeypatch.setattr("vibe.upgrade.os.access", lambda path, mode: True)

    plan = build_upgrade_plan(
        python_executable="/tmp/.local/share/uv/tools/vibe-remote/bin/python",
        uv_path="/usr/local/bin/uv",
        vibe_path="/custom/bin/vibe",
        base_env={"PATH": "/usr/bin"},
    )

    assert plan.command == [
        "/usr/local/bin/uv",
        "tool",
        "install",
        "/tmp/vibe_remote-9999.0.0-py3-none-any.whl",
        "--upgrade",
        "--force",
    ]


def test_build_upgrade_plan_finds_uv_outside_current_path(monkeypatch):
    monkeypatch.setattr(
        "vibe.upgrade.shutil.which",
        lambda command, path=None: None if command == "uv" else "/custom/bin/vibe",
    )
    monkeypatch.setattr(
        "vibe.upgrade.os.path.exists",
        lambda path: path in {"/home/test/.local/bin/uv", "/custom/bin/vibe"},
    )
    monkeypatch.setattr(
        "vibe.upgrade.os.access",
        lambda path, mode: path in {"/home/test/.local/bin/uv", "/custom/bin/vibe"},
    )

    plan = build_upgrade_plan(
        python_executable="/tmp/.local/share/uv/tools/vibe-remote/bin/python",
        vibe_path="/custom/bin/vibe",
        base_env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/home/test"},
    )

    assert plan.method == "uv"
    assert plan.command == ["/home/test/.local/bin/uv", "tool", "install", "vibe-remote", "--upgrade"]


def test_get_current_vibe_bin_dir_resolves_launcher_target(monkeypatch):
    monkeypatch.setattr("vibe.upgrade.os.path.exists", lambda path: True)
    monkeypatch.setattr("vibe.upgrade.os.access", lambda path, mode: True)
    monkeypatch.setattr(
        "vibe.upgrade.os.path.islink",
        lambda path: path in {"/usr/local/bin/vibe", "/home/test/.local/bin/vibe"},
    )
    monkeypatch.setattr(
        "vibe.upgrade.os.readlink",
        lambda path: {
            "/usr/local/bin/vibe": "/home/test/.local/bin/vibe",
            "/home/test/.local/bin/vibe": "/home/test/.local/share/uv/tools/vibe-remote/bin/vibe",
        }[path],
    )

    bin_dir = get_current_vibe_bin_dir(vibe_path="/usr/local/bin/vibe")

    assert bin_dir == "/home/test/.local/bin"


def test_get_latest_version_info_uses_override_metadata_url(monkeypatch, tmp_path):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text('{"info": {"version": "9999.0.0"}}', encoding="utf-8")
    monkeypatch.setenv("VIBE_UPDATE_METADATA_URL", metadata_path.as_uri())

    info = get_latest_version_info("2.2.0")

    assert info == {"current": "2.2.0", "latest": "9999.0.0", "has_update": True, "error": None}


def test_get_running_vibe_path_prefers_cached_launcher(monkeypatch):
    monkeypatch.setenv("VIBE_CURRENT_EXECUTABLE", "/custom/bin/vibe")
    monkeypatch.setattr("vibe.upgrade.os.path.exists", lambda path: True)
    monkeypatch.setattr("vibe.upgrade.os.access", lambda path, mode: True)
    monkeypatch.setattr("vibe.upgrade.shutil.which", lambda *args, **kwargs: "/other/bin/vibe")

    resolved = get_running_vibe_path(argv0="vibe")

    assert resolved == "/custom/bin/vibe"


def test_get_running_vibe_path_preserves_launcher_symlink(monkeypatch):
    monkeypatch.delenv("VIBE_CURRENT_EXECUTABLE", raising=False)
    monkeypatch.setattr("vibe.upgrade.os.path.exists", lambda path: True)
    monkeypatch.setattr("vibe.upgrade.os.access", lambda path, mode: True)
    monkeypatch.setattr(
        "vibe.upgrade.shutil.which",
        lambda *args, **kwargs: "/home/test/.local/bin/vibe",
    )

    resolved = get_running_vibe_path(argv0="vibe")

    assert resolved == "/home/test/.local/bin/vibe"


def test_get_running_vibe_path_skips_stale_cached_launcher(monkeypatch):
    monkeypatch.setenv("VIBE_CURRENT_EXECUTABLE", "/stale/bin/vibe")
    monkeypatch.setattr("vibe.upgrade.os.path.exists", lambda path: path != "/stale/bin/vibe")
    monkeypatch.setattr("vibe.upgrade.os.access", lambda path, mode: path != "/stale/bin/vibe")
    monkeypatch.setattr("vibe.upgrade.shutil.which", lambda *args, **kwargs: "/fresh/bin/vibe")

    resolved = get_running_vibe_path(argv0="vibe")

    assert resolved == "/fresh/bin/vibe"


def test_get_restart_command_falls_back_to_python_module(monkeypatch):
    monkeypatch.delenv("VIBE_CURRENT_EXECUTABLE", raising=False)
    monkeypatch.setattr("vibe.upgrade.shutil.which", lambda *args, **kwargs: None)

    command = get_restart_command(python_executable="/usr/bin/python3", argv0="python")

    assert command == ["/usr/bin/python3", "-c", "from vibe.cli import main; main()"]


def test_do_upgrade_uses_upgrade_plan_env_and_restarts(monkeypatch):
    plan = UpgradePlan(
        command=["/usr/local/bin/uv", "tool", "install", "vibe-remote", "--upgrade"],
        env={"UV_TOOL_BIN_DIR": "/custom/bin"},
        method="uv",
    )
    calls: dict[str, Any] = {}

    monkeypatch.setattr(api, "build_upgrade_plan", lambda **kwargs: plan)
    monkeypatch.setattr(api, "get_running_vibe_path", lambda: "/custom/bin/vibe")
    monkeypatch.setattr(api, "get_restart_command", lambda **kwargs: ["/custom/bin/vibe"])

    def fake_run(cmd, **kwargs):
        calls["run_cmd"] = cmd
        calls["run_kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="done", stderr="")

    def fake_popen(cmd, **kwargs):
        calls["popen_cmd"] = cmd
        calls["popen_kwargs"] = kwargs

        class _Proc:
            pass

        return _Proc()

    monkeypatch.setattr(api.subprocess, "run", fake_run)
    monkeypatch.setattr(api.subprocess, "Popen", fake_popen)
    result = api.do_upgrade(auto_restart=True)

    assert result["ok"] is True
    assert result["restarting"] is True
    assert calls["run_cmd"] == plan.command
    assert calls["run_kwargs"]["capture_output"] is True
    assert calls["run_kwargs"]["text"] is True
    assert calls["run_kwargs"]["timeout"] == 120
    assert calls["run_kwargs"]["env"] == plan.env
    safe_cwd = calls["run_kwargs"].get("cwd")
    assert safe_cwd and os.path.isabs(safe_cwd), f"subprocess.run cwd must be an absolute path, got {safe_cwd!r}"
    assert calls["popen_cmd"][0] == sys.executable
    assert calls["popen_cmd"][1] == "-c"
    assert "time.sleep(2.0)" in calls["popen_cmd"][2]
    assert "/custom/bin/vibe" in calls["popen_cmd"][2]
    assert calls["popen_kwargs"]["start_new_session"] is True
    popen_cwd = calls["popen_kwargs"].get("cwd")
    assert popen_cwd and os.path.isabs(popen_cwd), f"Popen cwd must be an absolute path, got {popen_cwd!r}"


def test_cmd_upgrade_uses_upgrade_plan_env(monkeypatch):
    plan = UpgradePlan(
        command=["/usr/local/bin/uv", "tool", "install", "vibe-remote", "--upgrade"],
        env={"UV_TOOL_BIN_DIR": "/custom/bin"},
        method="uv",
    )
    calls: dict[str, Any] = {}

    monkeypatch.setattr(cli, "get_latest_version", lambda: {"error": None, "has_update": True, "latest": "2.2.0"})
    monkeypatch.setattr(cli, "cache_running_vibe_path", lambda: "/custom/bin/vibe")
    monkeypatch.setattr(cli, "build_upgrade_plan", lambda **kwargs: plan)

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="done", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = cli.cmd_upgrade()

    assert result == 0
    assert calls["cmd"] == plan.command
    assert calls["kwargs"]["capture_output"] is True
    assert calls["kwargs"]["text"] is True
    assert calls["kwargs"]["env"] == plan.env
    assert "cwd" in calls["kwargs"], "subprocess.run must specify cwd to avoid stale venv cwd"
    assert os.path.isabs(calls["kwargs"]["cwd"]), f"cwd must be absolute, got {calls['kwargs']['cwd']!r}"


def test_cmd_upgrade_skips_install_when_already_latest(monkeypatch):
    monkeypatch.setattr(cli, "get_latest_version", lambda: {"error": None, "has_update": False, "latest": "2.2.0"})

    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called when already latest")

    monkeypatch.setattr(cli.subprocess, "run", fail_run)

    assert cli.cmd_upgrade() == 0


def test_get_safe_cwd_returns_absolute_existing_dir():
    cwd = get_safe_cwd()
    assert os.path.isabs(cwd)
    assert os.path.isdir(cwd)


def test_get_safe_cwd_falls_back_when_home_invalid(monkeypatch):
    monkeypatch.setenv("HOME", "/nonexistent_dir_for_test")
    cwd = get_safe_cwd()
    assert os.path.isabs(cwd)
    assert os.path.isdir(cwd)
    assert cwd != "/nonexistent_dir_for_test"
