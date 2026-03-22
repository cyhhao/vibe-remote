from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibe import api


def _full_config_payload() -> dict:
    return {
        "platform": "discord",
        "mode": "self_host",
        "version": "v2",
        "slack": {
            "bot_token": "",
            "app_token": None,
            "signing_secret": None,
            "team_id": None,
            "team_name": None,
            "app_id": None,
            "require_mention": False,
        },
        "discord": {
            "bot_token": "discord-token-1234567890",
            "application_id": None,
            "guild_allowlist": ["754776951587340359"],
            "guild_denylist": [],
            "require_mention": False,
        },
        "lark": {
            "app_id": "",
            "app_secret": "",
            "require_mention": False,
            "domain": "feishu",
        },
        "runtime": {
            "default_cwd": "/tmp/workdir",
            "log_level": "INFO",
        },
        "agents": {
            "default_backend": "codex",
            "opencode": {
                "enabled": True,
                "cli_path": "opencode",
                "default_agent": None,
                "default_model": None,
                "default_reasoning_effort": None,
                "error_retry_limit": 1,
            },
            "claude": {
                "enabled": True,
                "cli_path": "claude",
                "default_model": None,
            },
            "codex": {
                "enabled": True,
                "cli_path": "codex",
                "default_model": "gpt-5.4",
            },
        },
        "gateway": None,
        "ui": {
            "setup_host": "127.0.0.1",
            "setup_port": 5123,
            "open_browser": False,
        },
        "update": {
            "auto_update": False,
            "check_interval_minutes": 0,
            "idle_minutes": 30,
            "notify_slack": False,
        },
        "ack_mode": "reaction",
        "show_duration": True,
        "include_user_info": True,
        "reply_enhancements": True,
        "language": "en",
    }


def test_save_config_merges_partial_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    original = api.save_config(_full_config_payload())
    assert original.show_duration is True
    assert original.update.auto_update is False

    updated = api.save_config({"show_duration": False, "update": {"auto_update": True}})

    assert updated.show_duration is False
    assert updated.update.auto_update is True
    assert updated.platform == "discord"
    assert updated.discord is not None
    assert updated.discord.bot_token == "discord-token-1234567890"
    assert updated.runtime.default_cwd == "/tmp/workdir"


def test_save_config_accepts_typing_ack_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    updated = api.save_config({**_full_config_payload(), "ack_mode": "typing"})

    assert updated.ack_mode == "typing"
