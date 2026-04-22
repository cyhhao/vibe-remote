from __future__ import annotations

import json

from config import paths
from config.v2_settings import GuildSettings, SettingsStore
from modules.settings_manager import SettingsManager
from vibe import api


def _config_payload() -> dict:
    return {
        "platform": "discord",
        "platforms": {"enabled": ["discord"], "primary": "discord"},
        "mode": "self_host",
        "version": "v2",
        "discord": {
            "bot_token": "discord-token-1234567890",
            "require_mention": False,
        },
        "runtime": {"default_cwd": "_tmp", "log_level": "INFO"},
        "agents": {
            "default_backend": "opencode",
            "opencode": {"enabled": True, "cli_path": "opencode"},
            "claude": {"enabled": True, "cli_path": "claude"},
            "codex": {"enabled": True, "cli_path": "codex"},
        },
    }


def test_settings_store_persists_discord_guild_scope(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()

    store = SettingsStore.get_instance()
    store.set_guilds_for_platform(
        "discord",
        {
            "guild-1": GuildSettings(enabled=True),
            "guild-2": GuildSettings(enabled=False),
        },
    )
    store.save()
    SettingsStore.reset_instance()

    reloaded = SettingsStore.get_instance()

    assert reloaded.has_guild_scope_for_platform("discord") is True
    assert reloaded.is_guild_enabled("discord", "guild-1") is True
    assert reloaded.is_guild_enabled("discord", "guild-2") is False
    assert reloaded.is_guild_enabled("discord", "guild-3") is False


def test_discord_settings_manager_prefers_explicit_guild_scope(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()

    manager = SettingsManager(platform="discord")
    manager.set_enabled_guild_ids(["guild-1", "guild-2"])

    assert manager.has_guild_scope() is True
    assert manager.is_guild_enabled("guild-1") is True
    assert manager.is_guild_enabled("guild-3") is False


def test_save_config_moves_legacy_discord_allowlist_to_settings(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()

    saved = api.save_config(
        {
            **_config_payload(),
            "discord": {
                "bot_token": "discord-token-1234567890",
                "guild_allowlist": ["guild-1", "guild-2"],
                "guild_denylist": [],
                "require_mention": False,
            },
        }
    )
    payload = api.config_to_payload(saved)
    settings = api.get_settings("discord")
    saved_config = json.loads(paths.get_config_path().read_text(encoding="utf-8"))

    assert "guild_allowlist" not in payload["discord"]
    assert "guild_allowlist" not in saved_config["discord"]
    assert settings["guild_allowlist"] == ["guild-1", "guild-2"]
    assert settings["guilds"]["guild-1"]["enabled"] is True
