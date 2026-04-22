from types import SimpleNamespace

from config.v2_config import DiscordConfig, V2Config
from core.controller import Controller


def _config_payload(discord_payload: dict) -> dict:
    return {
        "platform": "discord",
        "platforms": {"enabled": ["discord"], "primary": "discord"},
        "mode": "self_host",
        "version": "v2",
        "discord": discord_payload,
        "runtime": {"default_cwd": "_tmp", "log_level": "INFO"},
        "agents": {
            "default_backend": "opencode",
            "opencode": {"enabled": True, "cli_path": "opencode"},
            "claude": {"enabled": True, "cli_path": "claude"},
            "codex": {"enabled": True, "cli_path": "codex"},
        },
    }


def test_refresh_config_updates_platform_message_settings(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    stale_discord_config = DiscordConfig(bot_token="discord-token", require_mention=True)
    controller = Controller.__new__(Controller)
    controller.config = V2Config.from_payload(
        _config_payload(
            {
                "bot_token": "discord-token",
                "require_mention": stale_discord_config.require_mention,
            }
        )
    )
    controller.im_clients = {"discord": SimpleNamespace(config=stale_discord_config)}
    controller._config_mtime = None

    latest_config = V2Config.from_payload(
        _config_payload(
            {
                "bot_token": "discord-token",
                "require_mention": False,
            }
        )
    )
    latest_config.save()

    controller._refresh_config_from_disk()

    assert stale_discord_config.require_mention is False
