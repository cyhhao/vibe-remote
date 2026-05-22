from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_config import V2Config
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
        "include_time_info": True,
        "include_user_info": True,
        "reply_enhancements": True,
        "show_pages_prompt": True,
        "language": "en",
    }


def test_save_config_merges_partial_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    original = api.save_config(_full_config_payload())
    assert original.show_duration is True
    assert original.include_time_info is True
    assert original.update.auto_update is False

    updated = api.save_config({"show_duration": False, "include_time_info": False, "update": {"auto_update": True}})

    assert updated.show_duration is False
    assert updated.include_time_info is False
    assert updated.update.auto_update is True
    assert updated.platform == "discord"
    assert updated.discord is not None
    assert updated.discord.bot_token == "discord-token-1234567890"
    assert updated.runtime.default_cwd == "/tmp/workdir"


def test_save_config_defaults_show_duration_to_false_for_new_config(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    payload = _full_config_payload()
    payload.pop("show_duration")

    created = api.save_config(payload)

    assert created.show_duration is False


def test_save_config_defaults_include_time_info_to_true_for_new_config(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    payload = _full_config_payload()
    payload.pop("include_time_info")

    created = api.save_config(payload)

    assert created.include_time_info is True


def test_save_config_accepts_typing_ack_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    updated = api.save_config({**_full_config_payload(), "ack_mode": "typing"})

    assert updated.ack_mode == "typing"


def test_save_config_merges_audio_asr_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    created = api.save_config(_full_config_payload())
    assert created.audio_asr.enabled is True
    assert created.audio_asr.enabled_configured is False
    assert created.audio_asr.echo_transcript is True

    updated = api.save_config({"audio_asr": {"enabled": False, "enabled_configured": True, "echo_transcript": False}})
    payload = api.config_to_payload(updated)

    assert updated.audio_asr.enabled is False
    assert updated.audio_asr.enabled_configured is True
    assert updated.audio_asr.echo_transcript is False
    assert updated.audio_asr.endpoint_path == "/v1/audio/transcriptions"
    assert payload["audio_asr"]["enabled"] is False
    assert payload["audio_asr"]["enabled_configured"] is True
    assert payload["audio_asr"]["echo_transcript"] is False


def test_save_config_marks_explicit_audio_asr_disable_patch(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    api.save_config(_full_config_payload())

    updated = api.save_config({"audio_asr": {"enabled": False}})

    assert updated.audio_asr.enabled is False
    assert updated.audio_asr.enabled_configured is True


def test_config_load_defaults_missing_audio_asr_to_enabled():
    payload = _full_config_payload()
    payload.pop("audio_asr", None)

    created = V2Config.from_payload(payload)

    assert created.audio_asr.enabled is True
    assert created.audio_asr.enabled_configured is False


def test_save_config_preserves_show_pages_prompt_toggle(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    created = api.save_config(_full_config_payload())
    assert created.show_pages_prompt is True

    updated = api.save_config({"show_pages_prompt": False})
    payload = api.config_to_payload(updated)

    assert updated.show_pages_prompt is False
    assert payload["show_pages_prompt"] is False


def test_config_load_defaults_missing_show_pages_prompt_to_enabled():
    payload = _full_config_payload()
    payload.pop("show_pages_prompt")

    created = V2Config.from_payload(payload)

    assert created.show_pages_prompt is True


def test_config_load_preserves_pre_upgrade_audio_asr_false_as_opt_out():
    payload = _full_config_payload()
    payload["audio_asr"] = {"enabled": False, "echo_transcript": True}

    created = V2Config.from_payload(payload)

    assert created.audio_asr.enabled is False
    assert created.audio_asr.enabled_configured is True


def test_save_config_preserves_explicit_audio_asr_opt_out(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    payload = _full_config_payload()
    payload["audio_asr"] = {
        "enabled": False,
        "enabled_configured": True,
        "echo_transcript": True,
    }

    created = api.save_config(payload)

    assert created.audio_asr.enabled is False
    assert created.audio_asr.enabled_configured is True


def test_config_to_payload_redacts_remote_access_secrets_and_save_preserves_them(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    payload = _full_config_payload()
    payload["remote_access"] = {
        "provider": "vibe_cloud",
        "vibe_cloud": {
            "enabled": True,
            "backend_url": "https://avibe.bot",
            "public_url": "https://alex.avibe.bot",
            "instance_id": "inst_123",
            "client_id": "vr_client_123",
            "issuer": "https://avibe.bot",
            "authorization_endpoint": "https://avibe.bot/oauth/authorize",
            "token_endpoint": "https://avibe.bot/oauth/token",
            "jwks_uri": "https://avibe.bot/oauth/jwks.json",
            "redirect_uri": "https://alex.avibe.bot/auth/callback",
            "tunnel_token": "tunnel-token",
            "instance_secret": "instance-secret",
            "session_secret": "session-secret",
        },
    }
    created = api.save_config(payload)

    redacted = api.config_to_payload(created)
    cloud_payload = redacted["remote_access"]["vibe_cloud"]
    updated = api.save_config({**redacted, "show_duration": False})

    assert "tunnel_token" not in cloud_payload
    assert "instance_secret" not in cloud_payload
    assert "session_secret" not in cloud_payload
    assert updated.remote_access.vibe_cloud.tunnel_token == "tunnel-token"
    assert updated.remote_access.vibe_cloud.instance_secret == "instance-secret"
    assert updated.remote_access.vibe_cloud.session_secret == "session-secret"


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
