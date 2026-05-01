from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from storage.db import SqliteInvalidationProbe, create_sqlite_engine
from storage.importer import ensure_sqlite_state
from storage.migrations import run_migrations


def test_run_migrations_creates_initial_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"

    run_migrations(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'",
            )
        }
        assert "alembic_version" in tables
        assert "channel_settings" in tables
        assert "agent_session_bindings" in tables
        assert "session_messages" in tables


def test_initial_migration_is_schema_snapshot() -> None:
    migration_path = Path("storage/alembic/versions/20260501_0001_initial_sqlite_state.py")

    source = migration_path.read_text(encoding="utf-8")

    assert "from storage.models" not in source
    assert "metadata.create_all" not in source


def test_ensure_sqlite_state_imports_json_once(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db_path = state_dir / "vibe.sqlite"
    _write_current_settings(state_dir / "settings.json")
    _write_current_sessions(state_dir / "sessions.json")
    _write_discovered_chats(state_dir / "discovered_chats.json")

    first = ensure_sqlite_state(db_path=db_path, state_dir=state_dir, primary_platform="slack")
    second = ensure_sqlite_state(db_path=db_path, state_dir=state_dir, primary_platform="slack")

    assert first.imported is True
    assert first.backup_path is not None
    assert (first.backup_path / "settings.json").exists()
    assert first.counts["channel_settings"] == 1
    assert first.counts["user_settings"] == 1
    assert first.counts["guild_settings"] == 1
    assert first.counts["bind_codes"] == 1
    assert first.counts["agent_session_bindings"] == 1
    assert first.counts["active_threads"] == 1
    assert first.counts["active_polls"] == 1
    assert first.counts["processed_messages"] == 2
    assert first.counts["discovered_chats"] == 1
    with sqlite3.connect(db_path) as conn:
        last_activity = conn.execute(
            "select value from schema_meta where key = 'sessions_last_activity'",
        ).fetchone()
    assert last_activity == ("2026-05-01T00:00:00+00:00",)

    assert second.imported is False
    assert second.backup_path is None
    assert second.counts == first.counts


def test_legacy_sessions_import_requires_platform_when_not_inferable(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db_path = state_dir / "vibe.sqlite"
    (state_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session_mappings": {
                    "C123": {
                        "codex": {
                            "1774074591.762089:/repo": "codex-session-1",
                        }
                    }
                },
                "active_polls": {
                    "opencode-session-1": {
                        "opencode_session_id": "opencode-session-1",
                        "base_session_id": "base-1",
                        "channel_id": "C123",
                        "thread_id": "1774074591.762089",
                        "settings_key": "C123",
                        "working_path": "/repo",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="primary_platform is required"):
        ensure_sqlite_state(db_path=db_path, state_dir=state_dir)

    with sqlite3.connect(db_path) as conn:
        marker = conn.execute(
            "select value from schema_meta where key = 'json_import_completed_at'",
        ).fetchone()
    assert marker is None


def test_legacy_settings_import_does_not_rewrite_source_json(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    settings_path = state_dir / "settings.json"
    original = json.dumps(
        {
            "channels": {
                "C123": {
                    "enabled": True,
                    "show_message_types": ["assistant"],
                    "custom_cwd": "/repo",
                }
            }
        },
        indent=2,
    )
    settings_path.write_text(original, encoding="utf-8")

    report = ensure_sqlite_state(db_path=state_dir / "vibe.sqlite", state_dir=state_dir, primary_platform="slack")

    assert report.imported is True
    assert report.counts["channel_settings"] == 1
    assert settings_path.read_text(encoding="utf-8") == original


def test_failed_json_import_does_not_mark_complete_and_can_retry(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db_path = state_dir / "vibe.sqlite"
    (state_dir / "settings.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        ensure_sqlite_state(db_path=db_path, state_dir=state_dir, primary_platform="slack")

    with sqlite3.connect(db_path) as conn:
        marker = conn.execute(
            "select value from schema_meta where key = 'json_import_completed_at'",
        ).fetchone()
    assert marker is None

    _write_current_settings(state_dir / "settings.json")
    report = ensure_sqlite_state(db_path=db_path, state_dir=state_dir, primary_platform="slack")

    assert report.imported is True
    assert report.counts["channel_settings"] == 1


def test_invalid_discovered_chats_import_does_not_mark_complete_and_can_retry(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db_path = state_dir / "vibe.sqlite"
    _write_current_settings(state_dir / "settings.json")
    _write_current_sessions(state_dir / "sessions.json")
    (state_dir / "discovered_chats.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        ensure_sqlite_state(db_path=db_path, state_dir=state_dir, primary_platform="slack")

    with sqlite3.connect(db_path) as conn:
        marker = conn.execute(
            "select value from schema_meta where key = 'json_import_completed_at'",
        ).fetchone()
    assert marker is None

    _write_discovered_chats(state_dir / "discovered_chats.json")
    report = ensure_sqlite_state(db_path=db_path, state_dir=state_dir, primary_platform="slack")

    assert report.imported is True
    assert report.counts["discovered_chats"] == 1


def test_malformed_discovered_chats_structure_does_not_mark_complete(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db_path = state_dir / "vibe.sqlite"
    _write_current_settings(state_dir / "settings.json")
    _write_current_sessions(state_dir / "sessions.json")
    (state_dir / "discovered_chats.json").write_text(
        json.dumps({"schema_version": 1, "platforms": {"telegram": ["not", "a", "map"]}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="platform 'telegram'"):
        ensure_sqlite_state(db_path=db_path, state_dir=state_dir, primary_platform="slack")

    with sqlite3.connect(db_path) as conn:
        marker = conn.execute(
            "select value from schema_meta where key = 'json_import_completed_at'",
        ).fetchone()
    assert marker is None

    _write_discovered_chats(state_dir / "discovered_chats.json")
    report = ensure_sqlite_state(db_path=db_path, state_dir=state_dir, primary_platform="slack")

    assert report.imported is True
    assert report.counts["discovered_chats"] == 1


def test_data_version_probe_detects_external_write(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    run_migrations(db_path)
    engine = create_sqlite_engine(db_path)
    try:
        with SqliteInvalidationProbe(engine) as probe:
            assert probe.has_external_write() is False
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "insert into schema_meta (key, value, updated_at) values ('probe', '1', 'now')"
                )
            assert probe.has_external_write() is True
            assert probe.has_external_write() is False
    finally:
        engine.dispose()


def _write_current_settings(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "scopes": {
                    "channel": {
                        "slack": {
                            "C123": {
                                "enabled": True,
                                "show_message_types": ["assistant", "toolcall"],
                                "custom_cwd": "/repo",
                                "routing": {"agent_backend": "codex", "codex_model": "gpt-5.4"},
                                "require_mention": False,
                            }
                        }
                    },
                    "guild": {"discord": {"G123": {"enabled": True}}},
                    "guild_policy": {"discord": {"default_enabled": False}},
                    "user": {
                        "slack": {
                            "U123": {
                                "display_name": "Alex",
                                "is_admin": True,
                                "bound_at": "2026-05-01T00:00:00+00:00",
                                "enabled": True,
                                "show_message_types": ["assistant"],
                                "custom_cwd": "/repo",
                                "routing": {"agent_backend": "opencode"},
                                "dm_chat_id": "D123",
                            }
                        }
                    },
                },
                "bind_codes": [
                    {
                        "code": "vr-abc123",
                        "type": "one_time",
                        "created_at": "2026-05-01T00:00:00+00:00",
                        "expires_at": None,
                        "is_active": True,
                        "used_by": ["U123"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_current_sessions(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "session_mappings": {
                    "slack::C123": {
                        "codex": {
                            "slack_1774074591.762089:/repo": "codex-session-1",
                        }
                    }
                },
                "active_slack_threads": {
                    "slack::C123": {
                        "C123": {
                            "1774074591.762089": 1774074591.762089,
                        }
                    }
                },
                "active_polls": {
                    "opencode-session-1": {
                        "opencode_session_id": "opencode-session-1",
                        "base_session_id": "base-1",
                        "channel_id": "C123",
                        "thread_id": "1774074591.762089",
                        "settings_key": "C123",
                        "working_path": "/repo",
                        "baseline_message_ids": ["m0"],
                        "seen_tool_calls": ["tool-1"],
                        "emitted_assistant_messages": ["m1"],
                        "started_at": 1774074591.0,
                        "typing_indicator_active": True,
                        "context_token": "ctx",
                        "processing_indicator": {"platform": "slack"},
                        "user_id": "U123",
                        "platform": "slack",
                    }
                },
                "processed_message_ts": {
                    "C123": {
                        "1774074591.762089": ["m1", "m2"],
                    }
                },
                "last_activity": "2026-05-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def _write_discovered_chats(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platforms": {
                    "telegram": {
                        "123": {
                            "name": "General",
                            "username": "general",
                            "chat_type": "supergroup",
                            "is_private": False,
                            "is_forum": True,
                            "supports_topics": True,
                            "last_seen_at": "2026-05-01T00:00:00+00:00",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
