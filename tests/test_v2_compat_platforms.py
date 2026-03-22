from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_compat import to_app_config
from config.v2_config import (
    AgentsConfig,
    ClaudeConfig,
    CodexConfig,
    DiscordConfig,
    OpenCodeConfig,
    PlatformsConfig,
    RuntimeConfig,
    SlackConfig,
    UiConfig,
    UpdateConfig,
    V2Config,
)


def test_to_app_config_preserves_enabled_platforms():
    config = V2Config(
        mode="self_host",
        version="v2",
        platform="discord",
        platforms=PlatformsConfig(enabled=["slack", "discord"], primary="discord"),
        slack=SlackConfig(bot_token="", app_token=None, signing_secret=None, team_id=None, team_name=None, app_id=None),
        runtime=RuntimeConfig(default_cwd=".", log_level="INFO"),
        agents=AgentsConfig(
            default_backend="opencode",
            opencode=OpenCodeConfig(enabled=True, cli_path="opencode"),
            claude=ClaudeConfig(enabled=True, cli_path="claude"),
            codex=CodexConfig(enabled=False, cli_path="codex"),
        ),
        discord=DiscordConfig(bot_token="discord-token"),
        ui=UiConfig(),
        update=UpdateConfig(),
    )

    compat = to_app_config(config)

    assert compat.platform == "discord"
    assert compat.platforms == {"enabled": ["slack", "discord"], "primary": "discord"}
    assert compat.enabled_platforms() == ["slack", "discord"]
