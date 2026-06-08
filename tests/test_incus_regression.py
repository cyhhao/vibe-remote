from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "incus_regression.py"
SPEC = importlib.util.spec_from_file_location("incus_regression", SCRIPT_PATH)
incus_regression = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = incus_regression
SPEC.loader.exec_module(incus_regression)


def test_master_target_uses_stable_project_instance_and_port() -> None:
    target = incus_regression.resolve_target(
        argparse.Namespace(
            target="master",
            slug=None,
            host_port=None,
            ui_host="127.0.0.1",
            ui_port=5123,
            worktree_port_start=15200,
            worktree_port_end=15399,
        ),
        Path("/tmp/repo"),
        dry_run=True,
    )

    assert target.project == "avr-master"
    assert target.instance == "avibe-master"
    assert target.host_port == 15130


def test_worktree_target_slug_includes_path_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(incus_regression, "branch_name", lambda repo_root: "feature/Show Runtime")
    target = incus_regression.resolve_target(
        argparse.Namespace(
            target="worktree",
            slug=None,
            host_port=15234,
            ui_host="127.0.0.1",
            ui_port=5123,
            worktree_port_start=15200,
            worktree_port_end=15399,
        ),
        Path("/tmp/repo-a"),
        dry_run=True,
    )

    assert target.project.startswith("avr-wt-feature-show-runtime-")
    assert target.instance.startswith("avibe-wt-feature-show-runtime-")
    assert target.host_port == 15234


def test_cloud_init_configures_systemd_service_without_source_code() -> None:
    data = incus_regression.cloud_init_user_data()

    assert "#cloud-config" in data
    assert "name: avibe" in data
    assert "Description=Avibe regression service" in data
    assert "Environment=VIBE_DEPLOYMENT_ENV=regression" in data
    assert "EnvironmentFile=-/etc/avibe-regression.env" in data
    assert "ExecStart=/opt/avibe/venv/bin/python scripts/incus_regression_supervisor.py" in data
    assert "/opt/avibe/source" in data
    assert "/home/avibe/.vibe_remote" in data


def test_project_config_marks_regression_target() -> None:
    target = incus_regression.RegressionTarget(
        target="worktree",
        slug="demo-branch",
        project="avr-wt-demo-branch",
        instance="avibe-wt-demo-branch",
        host_port=15200,
        ui_host="127.0.0.1",
        ui_port=5123,
    )

    config = incus_regression.project_create_config(target)

    assert "restricted=true" in config
    assert "restricted.devices.proxy=allow" in config
    assert "user.avibe_regression.target=worktree" in config
    assert "user.avibe_regression.host_port=15200" in config


def test_remote_ref_prefixes_resource_names_only() -> None:
    assert incus_regression.remote_ref("lab", "demo") == "lab:demo"
    assert incus_regression.remote_ref(None, "demo") == "demo"
    assert incus_regression.remote_ref("lab") == "lab:"
    assert incus_regression.optional_remote_ref(None) == []
    assert incus_regression.optional_remote_ref("lab") == ["lab:"]


def test_default_base_image_alias_is_not_remote_syntax() -> None:
    assert ":" not in incus_regression.DEFAULT_IMAGE


def test_proxy_device_uses_remote_instance_ref() -> None:
    target = incus_regression.RegressionTarget(
        target="master",
        slug="master",
        project="avr-master",
        instance="avibe-master",
        host_port=15130,
        ui_host="127.0.0.1",
        ui_port=5123,
    )

    args = incus_regression.proxy_device_args(target, remote="lab")

    assert args[3] == "lab:avibe-master"
    assert "listen=tcp:127.0.0.1:15130" in args
    assert "connect=tcp:127.0.0.1:5123" in args


def test_build_base_uses_publishable_temp_instance() -> None:
    commands = []

    class RecordingRunner:
        def __init__(self, *, dry_run=False):
            self.dry_run = dry_run

        def run(self, command, *, check=True, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0)

    args = argparse.Namespace(
        dry_run=True,
        remote=None,
        source_image="images:ubuntu/24.04/cloud",
        temp_instance="avibe-regression-base-build",
        image="avibe-regression-base-current",
        storage_pool="default",
        network="incusbr0",
    )

    original_runner = incus_regression.Runner
    try:
        incus_regression.Runner = RecordingRunner
        assert incus_regression.cmd_build_base(args) == 0
    finally:
        incus_regression.Runner = original_runner

    joined = "\n".join(" ".join(command) for command in commands)
    assert "--ephemeral" not in joined
    assert "incus launch images:ubuntu/24.04/cloud avibe-regression-base-build --storage default --network incusbr0" in joined
    assert "https://deb.nodesource.com/setup_20.x" in joined
    assert "npm install -g askill @anthropic-ai/claude-code @openai/codex" in joined
    assert "incus publish avibe-regression-base-build --alias avibe-regression-base-current" in joined


def test_source_exclude_drops_runtime_and_dependency_dirs() -> None:
    assert incus_regression.should_exclude(".runtime/state.json")
    assert incus_regression.should_exclude("ui/node_modules/pkg/index.js")
    assert incus_regression.should_exclude("ui/dist/assets/app.js")
    assert incus_regression.should_exclude("pkg/__pycache__/x.pyc")
    assert not incus_regression.should_exclude("vibe/ui_server.py")


def test_runtime_env_payload_maps_show_runtime_and_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THREE_REGRESSION_SHOW_RUNTIME_GITHUB_REF", "main")
    monkeypatch.setenv("THREE_REGRESSION_SLACK_CHANNEL", "C123")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    payload = incus_regression.runtime_env_payload().decode()

    assert "VIBE_SHOW_RUNTIME_SOURCE=github-source" in payload
    assert "VIBE_SHOW_RUNTIME_GITHUB_REF=main" in payload
    assert "THREE_REGRESSION_SLACK_CHANNEL=C123" in payload
    assert "OPENAI_API_KEY=sk-test" in payload


def test_load_env_file_accepts_export_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env.three-regression"
    env_file.write_text("export THREE_REGRESSION_SLACK_CHANNEL=C123\n", encoding="utf-8")
    monkeypatch.delenv("THREE_REGRESSION_SLACK_CHANNEL", raising=False)

    loaded = incus_regression.load_env_file(tmp_path, env_file)

    assert loaded == env_file
    assert incus_regression.os.environ["THREE_REGRESSION_SLACK_CHANNEL"] == "C123"


def test_prepare_state_skips_existing_state_without_reset() -> None:
    commands = []

    class RecordingRunner:
        dry_run = False

        def run(self, command, *, check=True, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0)

    target = incus_regression.RegressionTarget(
        target="master",
        slug="master",
        project="avr-master",
        instance="avibe-master",
        host_port=15130,
        ui_host="127.0.0.1",
        ui_port=5123,
    )

    incus_regression.run_prepare_state(RecordingRunner(), target, reset_mode="none", remote=None)

    joined = "\n".join(" ".join(command) for command in commands)
    assert "test -f /home/avibe/.avibe/config/config.json" in joined
    assert "prepare_three_regression.py" not in joined


def test_prepare_state_reseeds_when_reset_requested() -> None:
    commands = []

    class RecordingRunner:
        dry_run = False

        def run(self, command, *, check=True, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0)

    target = incus_regression.RegressionTarget(
        target="master",
        slug="master",
        project="avr-master",
        instance="avibe-master",
        host_port=15130,
        ui_host="127.0.0.1",
        ui_port=5123,
    )

    incus_regression.run_prepare_state(RecordingRunner(), target, reset_mode="config", remote=None)

    joined = "\n".join(" ".join(command) for command in commands)
    assert "rm -rf /home/avibe/.regression-seed" in joined
    assert "prepare_three_regression.py" in joined


def test_write_runtime_env_uses_stdin_not_command_line() -> None:
    commands = []
    inputs = []

    class RecordingRunner:
        dry_run = False

        def run(self, command, *, input_bytes=None, **kwargs):
            commands.append(command)
            inputs.append(input_bytes)
            return subprocess.CompletedProcess(command, 0)

    target = incus_regression.RegressionTarget(
        target="master",
        slug="master",
        project="avr-master",
        instance="avibe-master",
        host_port=15130,
        ui_host="127.0.0.1",
        ui_port=5123,
    )

    incus_regression.write_runtime_env(RecordingRunner(), target, remote="lab")

    assert commands[0][:5] == ["incus", "--project", "avr-master", "exec", "lab:avibe-master"]
    assert b"VIBE_SHOW_RUNTIME_SOURCE" in inputs[0]
    assert "OPENAI_API_KEY" not in " ".join(commands[0])


def test_cleanup_stale_deletes_missing_worktree_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = repo / ".runtime" / "incus-regression"
    runtime.mkdir(parents=True)
    (runtime / "worktrees.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "worktrees": {
                    "old": {
                        "path": str(tmp_path / "missing"),
                        "project": "avr-wt-old",
                        "instance": "avibe-wt-old",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    commands = []

    class RecordingRunner:
        def __init__(self, *, dry_run=False):
            self.dry_run = dry_run

        def run(self, command, *, check=True, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(incus_regression, "current_repo_root", lambda: repo)
    monkeypatch.setattr(incus_regression, "git_common_root", lambda repo_root: repo_root)
    monkeypatch.setattr(incus_regression, "Runner", RecordingRunner)

    exit_code = incus_regression.cmd_cleanup_stale(argparse.Namespace(yes=True, dry_run=False, remote=None))

    assert exit_code == 0
    assert ["incus", "--project", "avr-wt-old", "delete", "avibe-wt-old", "--force"] in commands
    payload = json.loads((runtime / "worktrees.json").read_text(encoding="utf-8"))
    assert payload["worktrees"] == {}
