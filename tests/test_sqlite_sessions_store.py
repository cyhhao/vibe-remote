from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from config import paths
from config.v2_sessions import ActivePollInfo, SessionState, SessionsStore
from storage.db import create_sqlite_engine
from storage.models import agent_sessions
from storage.sessions_service import SQLiteSessionsService


def test_sessions_store_uses_sqlite_without_rewriting_legacy_json(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    original = json.dumps(
        {
            "session_mappings": {
                "C123": {
                    "opencode": {
                        "slack_123.456:/repo": "session-old",
                    }
                }
            },
            "active_polls": {
                "oc-1": {
                    "opencode_session_id": "oc-1",
                    "base_session_id": "base-1",
                    "channel_id": "C123",
                    "thread_id": "123.456",
                    "settings_key": "slack::C123",
                    "working_path": "/repo",
                }
            },
        },
        indent=2,
    )
    sessions_path.write_text(original, encoding="utf-8")

    store = SessionsStore(sessions_path)
    try:
        store.migrate_active_polls("slack")
        store.migrate_session_mappings("slack")
        store.add_active_poll(
            ActivePollInfo(
                opencode_session_id="oc-2",
                base_session_id="base-2",
                channel_id="C999",
                thread_id="999.000",
                settings_key="C999",
                working_path="/repo",
                platform="slack",
            )
        )
    finally:
        store.close()

    reloaded = SessionsStore(sessions_path)
    try:
        assert reloaded.state.session_mappings["slack::C123"]["opencode"]["slack_123.456:/repo"] == "session-old"
        assert reloaded.state.active_polls["oc-1"]["settings_key"] == "C123"
        assert reloaded.state.active_polls["oc-1"]["platform"] == "slack"
        assert reloaded.get_active_poll("oc-2") is not None
        assert sessions_path.read_text(encoding="utf-8") == original
    finally:
        reloaded.close()


def test_sessions_store_reloads_external_sqlite_writes(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    store = SessionsStore(sessions_path)
    external = SQLiteSessionsService(tmp_path / "vibe.sqlite")
    try:
        assert store.get_active_poll("oc-external") is None

        external.save_state(
            SessionState(
                active_polls={
                    "oc-external": ActivePollInfo(
                        opencode_session_id="oc-external",
                        base_session_id="base",
                        channel_id="C1",
                        thread_id="t1",
                        settings_key="C1",
                        working_path="/repo",
                        platform="slack",
                    ).to_dict()
                }
            )
        )

        store.maybe_reload()

        poll = store.get_active_poll("oc-external")
        assert poll is not None
        assert poll.platform == "slack"
        assert poll.channel_id == "C1"
    finally:
        external.close()
        store.close()


def test_sqlite_sessions_service_preserves_agent_session_ids_on_save(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    service = SQLiteSessionsService(db_path)
    try:
        state = SessionState(
            session_mappings={
                "slack::C123": {
                    "codex": {
                        "slack_171717.123": "thread-native-1",
                    }
                }
            }
        )
        service.save_state(state)

        engine = create_sqlite_engine(db_path)
        try:
            with engine.connect() as conn:
                original_id = conn.execute(select(agent_sessions.c.id)).scalar_one()
        finally:
            engine.dispose()

        service.save_state(
            SessionState(
                session_mappings={
                    "slack::C123": {
                        "codex": {
                            "slack_171717.123": "thread-native-1",
                        }
                    }
                },
                active_polls={
                    "oc-1": ActivePollInfo(
                        opencode_session_id="oc-1",
                        base_session_id="base",
                        channel_id="C123",
                        thread_id="171717.123",
                        settings_key="C123",
                        working_path="/repo",
                        platform="slack",
                    ).to_dict()
                },
            )
        )

        engine = create_sqlite_engine(db_path)
        try:
            with engine.connect() as conn:
                saved_id = conn.execute(select(agent_sessions.c.id)).scalar_one()
        finally:
            engine.dispose()

        assert saved_id == original_id
    finally:
        service.close()


def test_sqlite_sessions_service_updates_logical_agent_session_on_save(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    service = SQLiteSessionsService(db_path)
    try:
        service.save_state(
            SessionState(
                session_mappings={
                    "slack::C123": {
                        "codex": {
                            "slack_171717.123": "thread-native-1",
                        }
                    }
                }
            )
        )
        service.save_state(
            SessionState(
                session_mappings={
                    "slack::C123": {
                        "codex": {
                            "slack_171717.123": "thread-native-2",
                        }
                    }
                }
            )
        )

        engine = create_sqlite_engine(db_path)
        try:
            with engine.connect() as conn:
                rows = conn.execute(select(agent_sessions.c.native_session_id)).scalars().all()
        finally:
            engine.dispose()

        assert rows == ["thread-native-2"]
        assert service.load_state().session_mappings["slack::C123"]["codex"]["slack_171717.123"] == "thread-native-2"
    finally:
        service.close()


def test_sqlite_sessions_service_reserves_then_binds_agent_session_id(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    service = SQLiteSessionsService(db_path)
    try:
        reserved_id = service.ensure_agent_session_id(
            scope_key="slack::C123",
            agent_name="codex",
            session_anchor="slack_171717.123",
        )
        assert reserved_id is not None
        assert service.load_state().session_mappings["slack::C123"]["codex"]["slack_171717.123"] == ""

        bound_id = service.bind_agent_session(
            scope_key="slack::C123",
            agent_name="codex",
            session_anchor="slack_171717.123",
            native_session_id="thread-native-1",
        )

        assert bound_id == reserved_id
        assert service.get_agent_session_row_id(
            scope_key="slack::C123",
            agent_name="codex",
            session_anchor="slack_171717.123",
        ) == reserved_id
        assert service.load_state().session_mappings["slack::C123"]["codex"]["slack_171717.123"] == "thread-native-1"
    finally:
        service.close()


def test_sessions_store_lifecycle_updates_in_memory_state(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    store = SessionsStore(sessions_path)
    try:
        reserved_id = store.ensure_agent_session_id("slack::C123", "codex", "slack_171717.123")
        assert reserved_id is not None
        assert store.state.session_mappings["slack::C123"]["codex"]["slack_171717.123"] == ""

        bound_id = store.bind_agent_session("slack::C123", "codex", "slack_171717.123", "thread-native-1")

        assert bound_id == reserved_id
        assert store.state.session_mappings["slack::C123"]["codex"]["slack_171717.123"] == "thread-native-1"
        assert store.get_agent_session_row_id("slack::C123", "codex", "slack_171717.123") == reserved_id
    finally:
        store.close()


def test_sessions_store_lifecycle_survives_followup_save(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    store = SessionsStore(sessions_path)
    try:
        reserved_id = store.ensure_agent_session_id("slack::C123", "opencode", "slack_171717.123:/repo")
        bound_id = store.bind_agent_session("slack::C123", "opencode", "slack_171717.123:/repo", "oc-session-1")
        store.add_active_poll(
            ActivePollInfo(
                opencode_session_id="oc-session-1",
                base_session_id="slack_171717.123",
                channel_id="C123",
                thread_id="171717.123",
                settings_key="C123",
                working_path="/repo",
                platform="slack",
            )
        )

        assert bound_id == reserved_id
        assert store.state.session_mappings["slack::C123"]["opencode"]["slack_171717.123:/repo"] == "oc-session-1"
    finally:
        store.close()

    reloaded = SessionsStore(sessions_path)
    try:
        assert (
            reloaded.state.session_mappings["slack::C123"]["opencode"]["slack_171717.123:/repo"] == "oc-session-1"
        )
        assert (
            reloaded.get_agent_session_row_id("slack::C123", "opencode", "slack_171717.123:/repo") == reserved_id
        )
    finally:
        reloaded.close()


def test_sessions_store_atomically_claims_processed_messages_across_instances(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    first = SessionsStore(sessions_path)
    second = SessionsStore(sessions_path)
    try:
        assert first.try_add_to_processed_set("C123", "171717.123", "171717.456") is True
        assert second.try_add_to_processed_set("C123", "171717.123", "171717.456") is False
        assert second.is_message_in_processed_set("C123", "171717.123", "171717.456") is True
    finally:
        first.close()
        second.close()


def test_sessions_store_atomically_claims_runtime_events_across_instances(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    first = SessionsStore(sessions_path)
    second = SessionsStore(sessions_path)
    try:
        assert first.try_record_runtime_event("slack_event", "T1:Ev123", {"event_id": "Ev123"}) is True
        assert second.try_record_runtime_event("slack_event", "T1:Ev123", {"event_id": "Ev123"}) is False
        assert second.try_record_runtime_event("slack_event", "T1:Ev124", {"event_id": "Ev124"}) is True
    finally:
        first.close()
        second.close()


def test_sessions_store_save_preserves_external_processed_claims(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    stale = SessionsStore(sessions_path)
    external = SessionsStore(sessions_path)
    try:
        assert external.try_add_to_processed_set("C123", "171717.123", "171717.456") is True

        stale.add_active_poll(
            ActivePollInfo(
                opencode_session_id="oc-stale",
                base_session_id="base",
                channel_id="C123",
                thread_id="171717.123",
                settings_key="C123",
                working_path="/repo",
                platform="slack",
            )
        )

        reloaded = SessionsStore(sessions_path)
        try:
            assert reloaded.is_message_in_processed_set("C123", "171717.123", "171717.456") is True
        finally:
            reloaded.close()
    finally:
        stale.close()
        external.close()


def test_sessions_store_save_keeps_newest_external_processed_claims(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    stale = SQLiteSessionsService(db_path)
    external = SQLiteSessionsService(db_path)
    try:
        stale_state = SessionState(
            processed_message_ts={
                "C123": {
                    "171717.123": [f"old-{index:03d}" for index in range(200)],
                }
            }
        )
        for index in range(5):
            assert external.try_record_processed_message("C123", "171717.123", f"new-{index:03d}") is True

        stale.save_state(stale_state)

        processed = stale.load_state().processed_message_ts["C123"]["171717.123"]
        assert len(processed) == 200
        assert processed[-5:] == [f"new-{index:03d}" for index in range(5)]
        assert "old-000" not in processed
    finally:
        stale.close()
        external.close()


def test_sessions_store_save_prunes_stale_processed_claim_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    service = SQLiteSessionsService(db_path)
    try:
        service.save_state(
            SessionState(
                processed_message_ts={
                    "C123": {
                        "171717.123": [f"msg-{index:03d}" for index in range(205)],
                    }
                }
            )
        )

        engine = create_sqlite_engine(db_path)
        try:
            with engine.connect() as conn:
                count = conn.execute(
                    select(agent_sessions.c.id)
                ).all()
                runtime_count = conn.exec_driver_sql(
                    "select count(*) from runtime_records where record_type = 'processed_message'"
                ).scalar_one()
        finally:
            engine.dispose()

        assert count == []
        assert runtime_count == 200
        processed = service.load_state().processed_message_ts["C123"]["171717.123"]
        assert processed[0] == "msg-005"
        assert processed[-1] == "msg-204"
    finally:
        service.close()


def test_sessions_store_hot_path_prunes_processed_claim_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    service = SQLiteSessionsService(db_path)
    try:
        for index in range(205):
            assert service.try_record_processed_message("C123", "171717.123", f"msg-{index:03d}") is True

        engine = create_sqlite_engine(db_path)
        try:
            with engine.connect() as conn:
                runtime_count = conn.exec_driver_sql(
                    "select count(*) from runtime_records where record_type = 'processed_message'"
                ).scalar_one()
        finally:
            engine.dispose()

        assert runtime_count == 200
        processed = service.load_state().processed_message_ts["C123"]["171717.123"]
        assert processed[0] == "msg-005"
        assert processed[-1] == "msg-204"
    finally:
        service.close()


def test_sessions_store_prunes_processed_claims_with_escaped_like_prefix(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    service = SQLiteSessionsService(db_path)
    try:
        for index in range(205):
            assert service.try_record_processed_message("C_1", "thread%1", f"msg-{index:03d}") is True
        assert service.try_record_processed_message("CA1", "threadX1", "other-thread-message") is True

        processed = service.load_state().processed_message_ts
        assert processed["C_1"]["thread%1"][0] == "msg-005"
        assert processed["C_1"]["thread%1"][-1] == "msg-204"
        assert processed["CA1"]["threadX1"] == ["other-thread-message"]
    finally:
        service.close()


def test_sessions_store_runtime_updates_do_not_flush_stale_snapshots(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    stale = SessionsStore(sessions_path)
    external = SessionsStore(sessions_path)
    try:
        stale.state.processed_message_ts = {
            "C123": {
                "171717.123": ["stale-message"],
            }
        }
        assert external.try_add_to_processed_set("C123", "171717.123", "external-message") is True

        stale.add_active_poll(
            ActivePollInfo(
                opencode_session_id="oc-stale",
                base_session_id="base",
                channel_id="C123",
                thread_id="171717.123",
                settings_key="C123",
                working_path="/repo",
                platform="slack",
            )
        )

        reloaded = SessionsStore(sessions_path)
        try:
            processed = reloaded._get_processed_set("C123", "171717.123")
            assert processed == ["external-message"]
            assert reloaded.get_active_poll("oc-stale") is not None
        finally:
            reloaded.close()
    finally:
        stale.close()
        external.close()


def test_sessions_store_bootstrap_uses_config_primary_platform(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    paths.get_config_path().write_text(
        json.dumps({"platform": "lark", "platforms": {"enabled": ["lark"], "primary": "lark"}}),
        encoding="utf-8",
    )
    paths.get_sessions_path().write_text(
        json.dumps(
            {
                "session_mappings": {"chat-1": {"codex": {"1774074591.762089:/repo": "session-1"}}},
                "active_polls": {
                    "oc-1": {
                        "opencode_session_id": "oc-1",
                        "base_session_id": "base-1",
                        "channel_id": "chat-1",
                        "thread_id": "1774074591.762089",
                        "settings_key": "chat-1",
                        "working_path": "/repo",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store = SessionsStore(paths.get_sessions_path())
    try:
        assert "lark::chat-1" in store.state.session_mappings
        assert store.state.active_polls["oc-1"]["platform"] == "lark"
    finally:
        store.close()


def test_sessions_store_custom_path_uses_sibling_config_primary_platform(tmp_path: Path) -> None:
    root = tmp_path / "custom-home"
    state_dir = root / "state"
    config_dir = root / "config"
    state_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"platform": "lark", "platforms": {"enabled": ["lark"], "primary": "lark"}}),
        encoding="utf-8",
    )
    sessions_path = state_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "session_mappings": {"chat-2": {"codex": {"1774074591.762089:/repo": "session-2"}}},
                "active_polls": {
                    "oc-2": {
                        "opencode_session_id": "oc-2",
                        "base_session_id": "base-2",
                        "channel_id": "chat-2",
                        "thread_id": "1774074591.762089",
                        "settings_key": "chat-2",
                        "working_path": "/repo",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store = SessionsStore(sessions_path)
    try:
        assert "lark::chat-2" in store.state.session_mappings
        assert store.state.active_polls["oc-2"]["platform"] == "lark"
    finally:
        store.close()


def test_sessions_store_preserves_legacy_non_string_session_values(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    store = SessionsStore(sessions_path)
    try:
        store.state.session_mappings = {"U1": {"claude": {"base": {"/repo": "session-1"}}}}
        store.save()
    finally:
        store.close()

    reloaded = SessionsStore(sessions_path)
    try:
        assert reloaded.state.session_mappings["U1"]["claude"]["base"]["/repo"] == "session-1"
    finally:
        reloaded.close()
