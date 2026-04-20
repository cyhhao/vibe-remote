from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_config import (
    AgentsConfig,
    DiscordConfig,
    LarkConfig,
    PlatformsConfig,
    RuntimeConfig,
    SlackConfig,
    TelegramConfig,
    UiConfig,
    UpdateConfig,
    V2Config,
    WeChatConfig,
)


def _base_config(**overrides) -> V2Config:
    payload = {
        "mode": "self_host",
        "version": "v2",
        "platform": "slack",
        "platforms": PlatformsConfig(enabled=["slack"], primary="slack"),
        "slack": SlackConfig(bot_token=""),
        "runtime": RuntimeConfig(default_cwd="."),
        "agents": AgentsConfig(),
        "ui": UiConfig(),
        "update": UpdateConfig(),
    }
    payload.update(overrides)
    return V2Config(**payload)


def test_platform_has_credentials_supports_telegram() -> None:
    config = _base_config(
        platform="telegram",
        platforms=PlatformsConfig(enabled=["telegram"], primary="telegram"),
        telegram=TelegramConfig(bot_token="123456:test-token"),
    )

    assert config.platform_has_credentials("telegram") is True
    assert config.configured_platforms() == ["telegram"]
    assert config.has_configured_platform_credentials() is True


def test_configured_platforms_only_counts_enabled_platforms() -> None:
    config = _base_config(
        platform="slack",
        platforms=PlatformsConfig(enabled=["slack"], primary="slack"),
        slack=SlackConfig(bot_token=""),
        discord=DiscordConfig(bot_token="configured-but-disabled"),
    )

    assert config.platform_has_credentials("discord") is True
    assert config.configured_platforms() == []
    assert config.has_configured_platform_credentials() is False


def test_configured_platforms_support_all_platform_types() -> None:
    config = _base_config(
        platform="slack",
        platforms=PlatformsConfig(enabled=["slack", "discord", "telegram", "lark", "wechat"], primary="slack"),
        slack=SlackConfig(bot_token="xoxb-test"),
        discord=DiscordConfig(bot_token="discord-token"),
        telegram=TelegramConfig(bot_token="123456:test-token"),
        lark=LarkConfig(app_id="app-id", app_secret="app-secret"),
        wechat=WeChatConfig(bot_token="wechat-token"),
    )

    assert config.configured_platforms() == ["slack", "discord", "telegram", "lark", "wechat"]


def test_platform_has_credentials_rejects_unknown_platform() -> None:
    config = _base_config()

    with pytest.raises(ValueError, match="Unsupported platform"):
        config.platform_has_credentials("unknown")
