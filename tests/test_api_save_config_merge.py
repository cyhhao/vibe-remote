from __future__ import annotations

import ast
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
            "disable_link_unfurl": False,
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
            "notify_admins": False,
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


def test_save_config_merges_remote_access_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    api.save_config(_full_config_payload())

    updated = api.save_config(
        {
            "remote_access": {
                "cloudflare": {
                    "enabled": True,
                    "hostname": "admin.example.com",
                    "tunnel_token": "tunnel-token",
                    "confirmed_access_policy": True,
                    "confirmed_tunnel_route": True,
                }
            }
        }
    )

    assert updated.remote_access.provider == "cloudflare"
    assert updated.remote_access.cloudflare.enabled is True
    assert updated.remote_access.cloudflare.hostname == "admin.example.com"
    assert updated.remote_access.cloudflare.tunnel_token == "tunnel-token"
    assert updated.remote_access.cloudflare.confirmed_access_policy is True
    assert updated.platform == "discord"


def test_save_config_merges_legacy_admin_access_over_existing_remote_access(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    api.save_config(_full_config_payload())
    api.save_config(
        {
            "remote_access": {
                "cloudflare": {
                    "enabled": False,
                    "hostname": "old.example.com",
                    "tunnel_token": "old-token",
                }
            }
        }
    )

    updated = api.save_config(
        {
            "admin_access": {
                "cloudflare": {
                    "enabled": True,
                    "hostname": "legacy.example.com",
                    "tunnel_token": "legacy-token",
                    "confirmed_access_policy": True,
                    "confirmed_tunnel_route": True,
                }
            }
        }
    )

    assert updated.remote_access.cloudflare.enabled is True
    assert updated.remote_access.cloudflare.hostname == "legacy.example.com"
    assert updated.remote_access.cloudflare.tunnel_token == "legacy-token"
    assert updated.remote_access.cloudflare.confirmed_access_policy is True
    assert updated.remote_access.cloudflare.confirmed_tunnel_route is True


def test_save_config_defaults_show_duration_to_false_for_new_config(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    payload = _full_config_payload()
    payload.pop("show_duration")

    created = api.save_config(payload)

    assert created.show_duration is False


def test_save_config_accepts_typing_ack_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    updated = api.save_config({**_full_config_payload(), "ack_mode": "typing"})

    assert updated.ack_mode == "typing"


def test_save_config_accepts_slack_disable_link_unfurl(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    payload = _full_config_payload()
    payload["slack"]["disable_link_unfurl"] = True

    updated = api.save_config(payload)

    assert updated.slack.disable_link_unfurl is True


def test_save_config_preserves_platforms_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    updated = api.save_config(
        {
            **_full_config_payload(),
            "wechat": {
                "corp_id": "wk123",
                "agent_id": "agent1",
                "secret": "sec",
                "token": "tok",
                "aes_key": "aes",
            },
            "platforms": {"enabled": ["slack", "discord", "wechat"], "primary": "discord"},
        }
    )

    assert updated.platform == "discord"
    assert updated.platforms.primary == "discord"
    assert updated.platforms.enabled == ["slack", "discord", "wechat"]


def test_save_config_migrates_legacy_single_platform(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    updated = api.save_config(_full_config_payload())
    payload = api.config_to_payload(updated)

    assert updated.platforms.primary == "discord"
    assert updated.platforms.enabled == ["discord"]
    assert payload["platforms"] == {"enabled": ["discord"], "primary": "discord"}


def test_save_config_rejects_enabled_platform_without_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    import pytest

    with pytest.raises(ValueError, match="wechat.*must be provided"):
        api.save_config(
            {
                **_full_config_payload(),
                "platforms": {"enabled": ["slack", "discord", "wechat"], "primary": "discord"},
                # wechat config intentionally omitted
            }
        )

def test_init_sessions_is_noop_when_sessions_file_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    store = api.SessionsStore()
    store.state.session_mappings = {
        "discord::749794605024936027": {
            "codex": {"discord_1482432040375943208": "019d1f70-692b-7c32-b152-b4aef9e24002"}
        }
    }
    store.save()

    api.init_sessions()

    reloaded = api.SessionsStore()
    reloaded.load()
    assert reloaded.state.session_mappings == store.state.session_mappings


def test_config_post_does_not_call_init_sessions():
    source = Path("vibe/ui_server.py").read_text(encoding="utf-8")
    module = ast.parse(source)

    config_post = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "config_post"
    )

    calls_init_sessions = False
    for node in ast.walk(config_post):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "api" and node.func.attr == "init_sessions":
                calls_init_sessions = True
                break

    assert calls_init_sessions is False
