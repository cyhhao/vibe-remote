from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from core.show_session_events import ShowSessionEventError, ShowSessionEventStore
from storage.db import create_sqlite_engine
from storage.importer import ensure_sqlite_state
from storage.models import agent_sessions, messages, show_session_events
from storage.settings_service import upsert_scope


@pytest.fixture()
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    yield tmp_path


def _seed_session(session_id: str = "ses_mark") -> str:
    from storage import messages_service

    engine = create_sqlite_engine()
    now = messages_service._utc_now_iso()
    with engine.begin() as conn:
        scope_id = upsert_scope(
            conn,
            platform="avibe",
            scope_type="project",
            native_id="proj_show_events",
            now=now,
        )
        conn.execute(
            agent_sessions.insert().values(
                id=session_id,
                scope_id=scope_id,
                agent_backend="codex",
                agent_variant="default",
                session_anchor="anchor_" + session_id,
                native_session_id="",
                status="active",
                metadata_json="{}",
                created_at=now,
                updated_at=now,
                last_active_at=now,
            )
        )
    return scope_id


def test_show_event_store_records_assistant_mark_and_transcript_message(isolated_state):
    _seed_session()
    store = ShowSessionEventStore()
    try:
        event = store.append(
            "ses_mark",
            {
                "type": "assistant.mark.created",
                "mark": {
                    "target": "mark-default-summary",
                    "body": "Review this summary again.",
                },
                "anchor": {
                    "selector": "[mark-default='summary']",
                    "text": "Quarterly summary",
                },
            },
        )
    finally:
        store.close()

    assert event["type"] == "assistant.mark.created"
    assert event["scope"] == "default"
    assert event["message_id"]
    assert "[agent-mark:default] mark-default-summary" in event["transcript_text"]
    assert "Anchor: [mark-default='summary']" in event["transcript_text"]

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        event_row = conn.execute(select(show_session_events)).mappings().one()
        message_row = conn.execute(select(messages).where(messages.c.id == event["message_id"])).mappings().one()

    assert event_row["id"] == event["id"]
    assert json.loads(event_row["payload_json"])["body"] == "Review this summary again."
    assert message_row["author"] == "agent"
    assert message_row["platform"] == "avibe"
    assert message_row["native_message_id"] == f"show:{event['id']}"
    assert "Review this summary again." in message_row["content_text"]


def test_show_event_store_rejects_unknown_session(isolated_state):
    store = ShowSessionEventStore()
    try:
        with pytest.raises(ShowSessionEventError) as raised:
            store.append(
                "ses_missing",
                {
                    "type": "assistant.mark.created",
                    "mark": {"target": "summary", "body": "body"},
                },
            )
    finally:
        store.close()

    assert raised.value.code == "session_not_found"


def test_show_event_store_lists_after_cursor(isolated_state):
    _seed_session()
    store = ShowSessionEventStore()
    try:
        first = store.append("ses_mark", {"type": "assistant.mark.created", "mark": {"target": "a", "body": "one"}})
        second = store.append("ses_mark", {"type": "assistant.mark.created", "mark": {"target": "b", "body": "two"}})
        page = store.list("ses_mark", after_id=first["id"])
    finally:
        store.close()

    assert [event["id"] for event in page["events"]] == [second["id"]]
