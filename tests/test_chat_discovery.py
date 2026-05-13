from __future__ import annotations

import json
from pathlib import Path

from config.v2_settings import ChannelSettings, SettingsState
from core import chat_discovery
from storage.migrations import run_migrations
from storage.settings_service import SQLiteSettingsService


def _auth_context(platform: str, **kwargs) -> str:
    value = chat_discovery._auth_context_for(platform, kwargs)
    assert value is not None
    return value


def test_metadata_merge_preserves_unknown_keys_and_sticky_true_flags() -> None:
    merged = chat_discovery.merge_metadata(
        {
            "custom": "keep",
            chat_discovery.METADATA_IS_FORUM: True,
            chat_discovery.METADATA_TOPIC: "old",
        },
        {
            chat_discovery.METADATA_IS_FORUM: False,
            chat_discovery.METADATA_TOPIC: "new",
            chat_discovery.METADATA_VISIBILITY_STATUS: chat_discovery.VISIBILITY_VISIBLE,
        },
    )

    assert merged["custom"] == "keep"
    assert merged[chat_discovery.METADATA_IS_FORUM] is True
    assert merged[chat_discovery.METADATA_TOPIC] == "new"
    assert merged[chat_discovery.METADATA_VISIBILITY_STATUS] == chat_discovery.VISIBILITY_VISIBLE


def test_remember_chat_lists_inventory_with_configured_state(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    run_migrations(db_path)

    chat_discovery.remember_chat(
        "telegram",
        "123",
        name="General",
        native_type="supergroup",
        is_private=False,
        supports_threads=True,
        metadata={
            chat_discovery.METADATA_USERNAME: "general",
            chat_discovery.METADATA_IS_FORUM: True,
        },
        db_path=db_path,
    )

    service = SQLiteSettingsService(db_path)
    try:
        service.save_state(SettingsState(channels={"telegram::123": ChannelSettings(enabled=True)}))
    finally:
        service.close()

    chats = chat_discovery.list_chats("telegram", db_path=db_path)

    assert len(chats) == 1
    assert chats[0].chat_id == "123"
    assert chats[0].name == "General"
    assert chats[0].configured is True
    assert chats[0].metadata[chat_discovery.METADATA_USERNAME] == "general"
    assert chats[0].visibility_status == chat_discovery.VISIBILITY_VISIBLE


def test_legacy_discovered_chats_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    run_migrations(db_path)
    legacy_path = tmp_path / "discovered_chats.json"
    legacy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platforms": {
                    "telegram": {
                        "456": {
                            "name": "Ops",
                            "username": "ops",
                            "chat_type": "supergroup",
                            "is_private": False,
                            "is_forum": True,
                            "supports_topics": True,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    chat_discovery.migrate_legacy_discovered_chats(db_path=db_path, legacy_path=legacy_path)
    chat_discovery.migrate_legacy_discovered_chats(db_path=db_path, legacy_path=legacy_path)

    chats = chat_discovery.list_chats("telegram", db_path=db_path)

    assert [chat.chat_id for chat in chats] == ["456"]
    assert chats[0].supports_threads is True
    assert legacy_path.exists() is False
    assert legacy_path.with_suffix(".json.migrated").exists()
    assert chat_discovery.get_state_meta("migrations.discovered_chats_to_scopes", db_path=db_path) == "done"


def test_refresh_marks_absent_rows_not_returned_without_deleting_settings(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "vibe.sqlite"
    run_migrations(db_path)
    chat_discovery.remember_chat(
        "slack",
        "C_OLD",
        name="old",
        metadata={chat_discovery.METADATA_AUTH_CONTEXT: _auth_context("slack", bot_token="x")},
        db_path=db_path,
    )

    service = SQLiteSettingsService(db_path)
    try:
        service.save_state(SettingsState(channels={"slack::C_OLD": ChannelSettings(enabled=True)}))
    finally:
        service.close()

    from vibe import api

    monkeypatch.setattr(
        api,
        "list_channels_live",
        lambda _token, browse_all=False: {
            "ok": True,
            "channels": [{"id": "C_NEW", "name": "new", "is_private": False}],
            "is_member_only": not browse_all,
        },
    )

    result = chat_discovery.refresh_platform("slack", force=True, bot_token="x", db_path=db_path)
    chats = {chat.chat_id: chat for chat in chat_discovery.list_chats("slack", db_path=db_path)}

    assert result.ok is True
    assert chats["C_NEW"].visibility_status == chat_discovery.VISIBILITY_VISIBLE
    assert chats["C_OLD"].visibility_status == chat_discovery.VISIBILITY_NOT_RETURNED
    assert chats["C_OLD"].configured is True


def test_refresh_failure_keeps_stale_cache_and_records_error(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "vibe.sqlite"
    run_migrations(db_path)
    chat_discovery.remember_chat(
        "slack",
        "C_KEEP",
        name="keep",
        metadata={chat_discovery.METADATA_AUTH_CONTEXT: _auth_context("slack", bot_token="x")},
        db_path=db_path,
    )

    from vibe import api

    monkeypatch.setattr(api, "list_channels_live", lambda _token, browse_all=False: {"ok": False, "error": "boom"})

    result = chat_discovery.refresh_platform("slack", force=True, bot_token="x", db_path=db_path)
    chats = chat_discovery.list_chats("slack", db_path=db_path)
    state = chat_discovery.refresh_state("slack", refresh_scope=_auth_context("slack", bot_token="x"), db_path=db_path)
    response = chat_discovery.channels_response("slack", bot_token="x", db_path=db_path)

    assert result.ok is False
    assert result.refresh_state.last_error == "boom"
    assert chats[0].chat_id == "C_KEEP"
    assert state.last_error == "boom"
    assert response["ok"] is True
    assert response["channels"][0]["id"] == "C_KEEP"
    assert response["error"] == "boom"


def test_empty_cache_channel_response_respects_refresh_backoff(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "vibe.sqlite"
    run_migrations(db_path)
    calls = 0

    from vibe import api

    def fail_refresh(_token: str, browse_all: bool = False) -> dict:
        nonlocal calls
        calls += 1
        return {"ok": False, "error": "bad token"}

    monkeypatch.setattr(api, "list_channels_live", fail_refresh)

    first = chat_discovery.channels_response("slack", bot_token="x", db_path=db_path)
    second = chat_discovery.channels_response("slack", bot_token="x", db_path=db_path)

    assert calls == 1
    assert first["ok"] is False
    assert first["error"] == "bad token"
    assert second["ok"] is False
    assert second["error"] == "bad token"


def test_slack_cached_response_respects_member_only_browse_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    run_migrations(db_path)
    auth_context = _auth_context("slack", bot_token="x")
    chat_discovery.remember_chat(
        "slack",
        "C_MEMBER",
        name="member",
        metadata={chat_discovery.METADATA_IS_MEMBER: True, chat_discovery.METADATA_AUTH_CONTEXT: auth_context},
        db_path=db_path,
    )
    chat_discovery.remember_chat(
        "slack",
        "C_OTHER",
        name="other",
        metadata={chat_discovery.METADATA_IS_MEMBER: False, chat_discovery.METADATA_AUTH_CONTEXT: auth_context},
        db_path=db_path,
    )
    chat_discovery.set_state_meta(
        f"channel_refresh.slack.{auth_context}",
        {"last_attempt_at": "2999-01-01T00:00:00+00:00", "last_success_at": "2999-01-01T00:00:00+00:00", "last_error": None},
        db_path=db_path,
    )

    member_only = chat_discovery.channels_response("slack", require_member=True, bot_token="x", db_path=db_path)
    browse_all = chat_discovery.channels_response("slack", require_member=False, bot_token="x", db_path=db_path)

    assert [channel["id"] for channel in member_only["channels"]] == ["C_MEMBER"]
    assert {channel["id"] for channel in browse_all["channels"]} == {"C_MEMBER", "C_OTHER"}


def test_slack_channel_cache_is_scoped_by_auth_context(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "vibe.sqlite"
    run_migrations(db_path)
    calls: list[str] = []

    from vibe import api

    def fake_list_channels_live(token: str, browse_all: bool = False) -> dict:
        calls.append(token)
        suffix = token[-1].upper()
        return {
            "ok": True,
            "channels": [{"id": f"C_{suffix}", "name": f"workspace-{suffix}", "is_private": False, "is_member": True}],
            "is_member_only": not browse_all,
        }

    monkeypatch.setattr(api, "list_channels_live", fake_list_channels_live)

    first = chat_discovery.channels_response("slack", bot_token="token-a", db_path=db_path)
    second = chat_discovery.channels_response("slack", bot_token="token-b", db_path=db_path)
    cached_first = chat_discovery.channels_response("slack", bot_token="token-a", db_path=db_path)

    assert calls == ["token-a", "token-b"]
    assert [channel["id"] for channel in first["channels"]] == ["C_A"]
    assert [channel["id"] for channel in second["channels"]] == ["C_B"]
    assert [channel["id"] for channel in cached_first["channels"]] == ["C_A"]


def test_discord_refresh_state_is_scoped_by_guild(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "vibe.sqlite"
    run_migrations(db_path)
    calls: list[str] = []

    from vibe import api

    def fake_discord_channels(_token: str, guild_id: str) -> dict:
        calls.append(guild_id)
        return {
            "ok": True,
            "channels": [{"id": f"C_{guild_id}", "name": f"channel-{guild_id}", "type": 0}],
        }

    monkeypatch.setattr(api, "discord_list_channels_live", fake_discord_channels)

    first = chat_discovery.refresh_platform("discord", bot_token="x", guild_id="G1", db_path=db_path)
    second = chat_discovery.refresh_platform("discord", bot_token="x", guild_id="G2", db_path=db_path)

    assert first.ok is True
    assert second.ok is True
    assert calls == ["G1", "G2"]
    assert (
        chat_discovery.refresh_state(
            "discord",
            refresh_scope=f"guild.G1.{_auth_context('discord', bot_token='x')}",
            db_path=db_path,
        ).last_success_at
        is not None
    )
    assert (
        chat_discovery.refresh_state(
            "discord",
            refresh_scope=f"guild.G2.{_auth_context('discord', bot_token='x')}",
            db_path=db_path,
        ).last_success_at
        is not None
    )
    assert chat_discovery.refresh_state("discord", db_path=db_path).last_success_at is None


def test_discord_cached_payload_preserves_numeric_channel_type(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "vibe.sqlite"
    run_migrations(db_path)

    from vibe import api

    monkeypatch.setattr(
        api,
        "discord_list_channels_live",
        lambda _token, _guild_id: {
            "ok": True,
            "channels": [{"id": "C_TEXT", "name": "text", "type": 0}],
        },
    )

    response = chat_discovery.channels_response(
        "discord",
        bot_token="x",
        guild_id="G1",
        parent_scope_id="discord::guild::G1",
        db_path=db_path,
    )
    cached = chat_discovery.channels_response(
        "discord",
        bot_token="x",
        guild_id="G1",
        parent_scope_id="discord::guild::G1",
        db_path=db_path,
    )

    assert response["channels"][0]["type"] == 0
    assert cached["channels"][0]["type"] == 0
    assert cached["channels"][0]["native_type"] == "0"


def test_malformed_legacy_discovered_chats_does_not_break_channel_response(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "vibe.sqlite"
    run_migrations(db_path)
    legacy_path = tmp_path / "discovered_chats.json"
    legacy_path.write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(chat_discovery.paths, "get_discovered_chats_path", lambda: legacy_path)

    from vibe import api

    monkeypatch.setattr(
        api,
        "list_channels_live",
        lambda _token, browse_all=False: {
            "ok": True,
            "channels": [{"id": "C1", "name": "general", "is_private": False, "is_member": True}],
            "is_member_only": not browse_all,
        },
    )

    response = chat_discovery.channels_response("slack", bot_token="x", require_member=True, db_path=db_path)

    assert response["ok"] is True
    assert response["channels"][0]["id"] == "C1"
    assert legacy_path.exists()
    assert chat_discovery.get_state_meta("migrations.discovered_chats_to_scopes", db_path=db_path) is None
