from __future__ import annotations

import json
from pathlib import Path

from config import paths
from config.v2_sessions import ActivePollInfo, SessionState, SessionsStore
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
