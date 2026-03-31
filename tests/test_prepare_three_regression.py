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
        "THREE_REGRESSION_UI_HOST": "192.168.2.3",
        "THREE_REGRESSION_DEFAULT_CWD": "/data/vibe_remote/workdir",
        "THREE_REGRESSION_DEFAULT_BACKEND": "opencode",
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
        "THREE_REGRESSION_WECHAT_BACKEND": "opencode",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_prepare_generates_unified_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module()
    _set_required_env(monkeypatch)

    module.prepare(tmp_path)

    config = json.loads((tmp_path / "vibe" / "config" / "config.json").read_text(encoding="utf-8"))
    settings = json.loads((tmp_path / "vibe" / "state" / "settings.json").read_text(encoding="utf-8"))

    # Unified config has all four platforms enabled
    assert config["platforms"]["enabled"] == ["slack", "discord", "lark", "wechat"]
    assert config["platforms"]["primary"] == "slack"

    # All platform credentials populated
    assert config["slack"]["bot_token"] == "xoxb-test-token"
    assert config["discord"]["bot_token"] == "discord-token-1234567890"
    assert config["discord"]["guild_allowlist"] == ["754776951587340359"]
    assert config["lark"]["app_id"] == "cli_test_app_id"
    assert config["wechat"]["base_url"] == "https://ilinkai.weixin.qq.com"

    # All three backends enabled
    assert config["agents"]["opencode"]["enabled"] is True
    assert config["agents"]["claude"]["enabled"] is True
    assert config["agents"]["codex"]["enabled"] is True
    assert config["agents"]["default_backend"] == "opencode"

    # UI host propagated
    assert config["ui"]["setup_host"] == "192.168.2.3"

    # Per-channel routing in settings for each platform
    assert settings["scopes"]["channel"]["slack"]["C123SLACK"]["routing"]["agent_backend"] == "opencode"
    assert settings["scopes"]["channel"]["discord"]["123456789012345678"]["routing"]["agent_backend"] == "codex"
    assert settings["scopes"]["channel"]["lark"]["oc_test_chat_id"]["routing"]["agent_backend"] == "claude"

    # WeChat has no channel set, so scope is empty
    assert settings["scopes"]["channel"]["wechat"] == {}

    # Directory structure
    assert (tmp_path / "vibe" / "workdir").is_dir()
    assert (tmp_path / "vibe" / "state" / "sessions.json").exists()

    # Shared agent home configs
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
    assert opencode_config["permission"] == "allow"
    assert opencode_config["provider"]["openai"]["options"]["baseURL"] == "https://ai-relay.example/v1"


def test_prepare_preserves_existing_state_without_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module()
    _set_required_env(monkeypatch)

    vibe_dir = tmp_path / "vibe"
    (vibe_dir / "config").mkdir(parents=True)
    (vibe_dir / "state").mkdir(parents=True)
    (vibe_dir / "config" / "config.json").write_text('{"keep": true}', encoding="utf-8")
    (vibe_dir / "state" / "settings.json").write_text('{"custom": true}', encoding="utf-8")
    (vibe_dir / "state" / "sessions.json").write_text('{"session": true}', encoding="utf-8")

    module.prepare(tmp_path)

    assert json.loads((vibe_dir / "config" / "config.json").read_text(encoding="utf-8")) == {"keep": True}
    assert json.loads((vibe_dir / "state" / "settings.json").read_text(encoding="utf-8")) == {"custom": True}
    assert json.loads((vibe_dir / "state" / "sessions.json").read_text(encoding="utf-8")) == {"session": True}


def test_prepare_allows_missing_channel_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module()
    _set_required_env(monkeypatch)
    monkeypatch.delenv("THREE_REGRESSION_SLACK_CHANNEL", raising=False)
    monkeypatch.delenv("THREE_REGRESSION_DISCORD_CHANNEL", raising=False)
    monkeypatch.delenv("THREE_REGRESSION_FEISHU_CHAT_ID", raising=False)
    monkeypatch.delenv("THREE_REGRESSION_WECHAT_CHANNEL", raising=False)

    module.prepare(tmp_path, reset_mode="config")

    settings = json.loads((tmp_path / "vibe" / "state" / "settings.json").read_text(encoding="utf-8"))
    assert settings["scopes"]["channel"]["slack"] == {}
    assert settings["scopes"]["channel"]["discord"] == {}
    assert settings["scopes"]["channel"]["lark"] == {}
    assert settings["scopes"]["channel"]["wechat"] == {}


def test_prepare_requires_supported_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = _load_module()
    _set_required_env(monkeypatch)
    monkeypatch.setenv("THREE_REGRESSION_SLACK_BACKEND", "unknown")

    with pytest.raises(SystemExit, match="THREE_REGRESSION_SLACK_BACKEND"):
        module.prepare(tmp_path)


def test_prepare_reset_config_preserves_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module()
    _set_required_env(monkeypatch)

    workdir = tmp_path / "vibe" / "workdir"
    workdir.mkdir(parents=True)
    (workdir / "keep.txt").write_text("keep-me", encoding="utf-8")
    config_dir = tmp_path / "vibe" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text('{"stale": true}', encoding="utf-8")

    module.prepare(tmp_path, reset_mode="config")

    assert (workdir / "keep.txt").read_text(encoding="utf-8") == "keep-me"
    refreshed = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
    assert refreshed["agents"]["default_backend"] == "opencode"


def test_prepare_reset_all_clears_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module()
    _set_required_env(monkeypatch)

    workdir = tmp_path / "vibe" / "workdir"
    workdir.mkdir(parents=True)
    (workdir / "drop.txt").write_text("remove-me", encoding="utf-8")

    module.prepare(tmp_path, reset_mode="all")

    assert not (workdir / "drop.txt").exists()


def test_prepare_default_backend_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module()
    _set_required_env(monkeypatch)
    monkeypatch.setenv("THREE_REGRESSION_DEFAULT_BACKEND", "claude")

    module.prepare(tmp_path, reset_mode="config")

    config = json.loads((tmp_path / "vibe" / "config" / "config.json").read_text(encoding="utf-8"))
    assert config["agents"]["default_backend"] == "claude"


def test_prepare_all_platform_channel_routing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module()
    _set_required_env(monkeypatch)
    monkeypatch.setenv("THREE_REGRESSION_WECHAT_CHANNEL", "wx_test_room")
    monkeypatch.setenv("THREE_REGRESSION_WECHAT_BACKEND", "codex")

    module.prepare(tmp_path, reset_mode="config")

    settings = json.loads((tmp_path / "vibe" / "state" / "settings.json").read_text(encoding="utf-8"))
    assert settings["scopes"]["channel"]["wechat"]["wx_test_room"]["routing"]["agent_backend"] == "codex"
    assert settings["scopes"]["channel"]["slack"]["C123SLACK"]["routing"]["agent_backend"] == "opencode"
