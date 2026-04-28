from __future__ import annotations

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
from vibe import api


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


def test_setup_state_counts_telegram_credentials() -> None:
    config = _base_config(
        platform="telegram",
        platforms=PlatformsConfig(enabled=["telegram"], primary="telegram"),
        telegram=TelegramConfig(bot_token="123456:test-token"),
    )

    assert config.platform_has_credentials("telegram") is True
    assert config.configured_platforms() == ["telegram"]
    assert config.setup_state() == {
        "needs_setup": False,
        "configured_platforms": ["telegram"],
        "missing_credentials": [],
    }


def test_setup_state_only_counts_enabled_platforms() -> None:
    config = _base_config(
        platform="slack",
        platforms=PlatformsConfig(enabled=["slack"], primary="slack"),
        slack=SlackConfig(bot_token=""),
        discord=DiscordConfig(bot_token="configured-but-disabled"),
    )

    assert config.platform_has_credentials("discord") is True
    assert config.configured_platforms() == []
    assert config.setup_state()["needs_setup"] is True


def test_config_payload_includes_platform_catalog_and_setup_state() -> None:
    config = _base_config(
        platforms=PlatformsConfig(enabled=["slack", "discord", "telegram", "lark", "wechat"], primary="slack"),
        slack=SlackConfig(bot_token="xoxb-test"),
        discord=DiscordConfig(bot_token="discord-token"),
        telegram=TelegramConfig(bot_token="123456:test-token"),
        lark=LarkConfig(app_id="app-id", app_secret="app-secret"),
        wechat=WeChatConfig(bot_token="wechat-token"),
    )

    payload = api.config_to_payload(config)

    assert [platform["id"] for platform in payload["platform_catalog"]] == [
        "slack",
        "discord",
        "telegram",
        "lark",
        "wechat",
    ]
    assert payload["setup_state"]["configured_platforms"] == ["slack", "discord", "telegram", "lark", "wechat"]
    assert payload["setup_state"]["needs_setup"] is False


def test_config_payload_includes_vibe_cloud_remote_access() -> None:
    config = _base_config()
    config.remote_access.vibe_cloud.enabled = True
    config.remote_access.vibe_cloud.public_url = "https://alex.avibe.bot"
    config.remote_access.vibe_cloud.instance_id = "inst_123"

    payload = api.config_to_payload(config)

    assert payload["remote_access"]["provider"] == "vibe_cloud"
    assert payload["remote_access"]["vibe_cloud"]["enabled"] is True
    assert payload["remote_access"]["vibe_cloud"]["public_url"] == "https://alex.avibe.bot"
