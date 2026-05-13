from __future__ import annotations

import json
from pathlib import Path

from config.v2_settings import ChannelSettings, SettingsState
from core import chat_discovery
from storage.migrations import run_migrations
from storage.settings_service import SQLiteSettingsService


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
    chat_discovery.remember_chat("slack", "C_OLD", name="old", db_path=db_path)

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
    chat_discovery.remember_chat("slack", "C_KEEP", name="keep", db_path=db_path)

    from vibe import api

    monkeypatch.setattr(api, "list_channels_live", lambda _token, browse_all=False: {"ok": False, "error": "boom"})

    result = chat_discovery.refresh_platform("slack", force=True, bot_token="x", db_path=db_path)
    chats = chat_discovery.list_chats("slack", db_path=db_path)
    state = chat_discovery.refresh_state("slack", db_path=db_path)
    response = chat_discovery.channels_response("slack", bot_token="x", db_path=db_path)

    assert result.ok is False
    assert result.refresh_state.last_error == "boom"
    assert chats[0].chat_id == "C_KEEP"
    assert state.last_error == "boom"
    assert response["ok"] is True
    assert response["channels"][0]["id"] == "C_KEEP"
    assert response["error"] == "boom"
