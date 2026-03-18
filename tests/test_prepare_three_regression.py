from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "prepare_three_regression.py"
    spec = importlib.util.spec_from_file_location("prepare_three_regression", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "ANTHROPIC_BASE_URL": "https://anthropic.example/v1",
        "OPENAI_API_KEY": "sk-openai-test",
        "OPENAI_BASE_URL": "https://openai.example",
        "OPENAI_API_BASE": "https://openai.example/v1",
        "THREE_REGRESSION_DEFAULT_CWD": "/data/vibe_remote/workdir",
        "THREE_REGRESSION_LOG_LEVEL": "DEBUG",
        "THREE_REGRESSION_LANGUAGE": "en",
        "THREE_REGRESSION_CLAUDE_BASE_URL": "https://ai-relay.example",
        "THREE_REGRESSION_CLAUDE_AUTH_TOKEN": "sk-claude-auth-token",
        "THREE_REGRESSION_CLAUDE_ATTRIBUTION_HEADER": "0",
        "THREE_REGRESSION_CODEX_MODEL": "gpt-5.4",
        "THREE_REGRESSION_CODEX_REVIEW_MODEL": "gpt-5.4",
        "THREE_REGRESSION_CODEX_REASONING_EFFORT": "xhigh",
        "THREE_REGRESSION_CODEX_BASE_URL": "https://ai-relay.example",
        "THREE_REGRESSION_CODEX_OPENAI_API_KEY": "sk-codex-openai",
        "THREE_REGRESSION_OPENCODE_OPENAI_BASE_URL": "https://ai-relay.example/v1",
        "THREE_REGRESSION_OPENCODE_OPENAI_API_KEY": "sk-opencode-openai",
        "THREE_REGRESSION_OPENCODE_ANTHROPIC_BASE_URL": "https://ai-relay.example/v1",
        "THREE_REGRESSION_OPENCODE_ANTHROPIC_API_KEY": "sk-opencode-anthropic",
        "THREE_REGRESSION_SLACK_BOT_TOKEN": "xoxb-test-token",
        "THREE_REGRESSION_SLACK_APP_TOKEN": "xapp-test-token",
        "THREE_REGRESSION_SLACK_CHANNEL": "C123SLACK",
        "THREE_REGRESSION_SLACK_BACKEND": "opencode",
        "THREE_REGRESSION_DISCORD_BOT_TOKEN": "discord-token-1234567890",
        "THREE_REGRESSION_DISCORD_CHANNEL": "123456789012345678",
        "THREE_REGRESSION_DISCORD_GUILD_ALLOWLIST": "754776951587340359",
        "THREE_REGRESSION_DISCORD_BACKEND": "codex",
        "THREE_REGRESSION_FEISHU_APP_ID": "cli_test_app_id",
        "THREE_REGRESSION_FEISHU_APP_SECRET": "test-app-secret",
        "THREE_REGRESSION_FEISHU_CHAT_ID": "oc_test_chat_id",
        "THREE_REGRESSION_FEISHU_BACKEND": "claude",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_prepare_generates_three_isolated_states(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module()
    _set_required_env(monkeypatch)

    module.prepare(tmp_path)

    slack_config = json.loads((tmp_path / "slack" / "config" / "config.json").read_text(encoding="utf-8"))
    discord_config = json.loads((tmp_path / "discord" / "config" / "config.json").read_text(encoding="utf-8"))
    feishu_settings = json.loads((tmp_path / "feishu" / "state" / "settings.json").read_text(encoding="utf-8"))

    assert slack_config["platform"] == "slack"
    assert slack_config["agents"]["default_backend"] == "opencode"
    assert discord_config["platform"] == "discord"
    assert discord_config["agents"]["default_backend"] == "codex"
    assert discord_config["discord"]["guild_allowlist"] == ["754776951587340359"]
    assert feishu_settings["scopes"]["channel"]["lark"]["oc_test_chat_id"]["routing"]["agent_backend"] == "claude"
    assert (tmp_path / "slack" / "workdir").is_dir()
    assert (tmp_path / "discord" / "state" / "sessions.json").exists()
    assert (
        json.loads((tmp_path / "shared-home" / ".claude" / "settings.json").read_text(encoding="utf-8"))["env"][
            "ANTHROPIC_AUTH_TOKEN"
        ]
        == "sk-claude-auth-token"
    )
    codex_config = (tmp_path / "shared-home" / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-5.4"' in codex_config
    assert "responses_websockets_v2 = false" in codex_config
    assert "suppress_unstable_features_warning = true" in codex_config
    opencode_config = json.loads(
        (tmp_path / "shared-home" / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8")
    )
    assert opencode_config["provider"]["openai"]["options"]["baseURL"] == "https://ai-relay.example/v1"


def test_prepare_preserves_existing_state_without_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module()
    _set_required_env(monkeypatch)

    service_dir = tmp_path / "slack"
    (service_dir / "config").mkdir(parents=True)
    (service_dir / "state").mkdir(parents=True)
    (service_dir / "config" / "config.json").write_text('{"keep": true}', encoding="utf-8")
    (service_dir / "state" / "settings.json").write_text('{"custom": true}', encoding="utf-8")
    (service_dir / "state" / "sessions.json").write_text('{"session": true}', encoding="utf-8")

    module.prepare(tmp_path)

    assert json.loads((service_dir / "config" / "config.json").read_text(encoding="utf-8")) == {"keep": True}
    assert json.loads((service_dir / "state" / "settings.json").read_text(encoding="utf-8")) == {"custom": True}
    assert json.loads((service_dir / "state" / "sessions.json").read_text(encoding="utf-8")) == {"session": True}


def test_prepare_allows_missing_channel_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module()
    _set_required_env(monkeypatch)
    monkeypatch.delenv("THREE_REGRESSION_SLACK_CHANNEL", raising=False)
    monkeypatch.delenv("THREE_REGRESSION_DISCORD_CHANNEL", raising=False)
    monkeypatch.delenv("THREE_REGRESSION_FEISHU_CHAT_ID", raising=False)

    module.prepare(tmp_path, reset_state=True)

    slack_settings = json.loads((tmp_path / "slack" / "state" / "settings.json").read_text(encoding="utf-8"))
    discord_settings = json.loads((tmp_path / "discord" / "state" / "settings.json").read_text(encoding="utf-8"))
    feishu_settings = json.loads((tmp_path / "feishu" / "state" / "settings.json").read_text(encoding="utf-8"))

    assert slack_settings["scopes"]["channel"]["slack"] == {}
    assert discord_settings["scopes"]["channel"]["discord"] == {}
    assert feishu_settings["scopes"]["channel"]["lark"] == {}


def test_prepare_requires_supported_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = _load_module()
    _set_required_env(monkeypatch)
    monkeypatch.setenv("THREE_REGRESSION_SLACK_BACKEND", "unknown")

    with pytest.raises(SystemExit, match="THREE_REGRESSION_SLACK_BACKEND"):
        module.prepare(tmp_path)
