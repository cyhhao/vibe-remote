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
    last_active_at = "2000-01-01T00:00:00Z"
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
                last_active_at=last_active_at,
            )
        )
    return scope_id


def test_show_event_store_records_assistant_mark_and_transcript_message(isolated_state):
    _seed_session()
    engine = create_sqlite_engine()
    with engine.connect() as conn:
        previous_active_at = conn.execute(
            select(agent_sessions.c.last_active_at).where(agent_sessions.c.id == "ses_mark")
        ).scalar_one()

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
    assert event["scope_id"]
    assert event["scope"] == "default"
    assert event["message_id"]
    assert event["message"]["id"] == event["message_id"]
    assert "[agent-mark:default:created] mark-default-summary" in event["transcript_text"]
    assert "Anchor: [mark-default='summary']" in event["transcript_text"]

    with engine.connect() as conn:
        event_row = conn.execute(select(show_session_events)).mappings().one()
        message_row = conn.execute(select(messages).where(messages.c.id == event["message_id"])).mappings().one()
        last_active_at = conn.execute(
            select(agent_sessions.c.last_active_at).where(agent_sessions.c.id == "ses_mark")
        ).scalar_one()

    assert event_row["id"] == event["id"]
    assert json.loads(event_row["payload_json"])["body"] == "Review this summary again."
    assert message_row["author"] == "agent"
    assert message_row["platform"] == "avibe"
    assert message_row["native_message_id"] == f"show:{event['id']}"
    assert "Review this summary again." in message_row["content_text"]
    assert last_active_at != previous_active_at


def test_show_event_store_records_human_annotation_with_anchor_context(isolated_state):
    _seed_session()

    store = ShowSessionEventStore()
    try:
        event = store.append(
            "ses_mark",
            {
                "type": "human.annotation.created",
                "annotation": {
                    "intent": "question",
                    "severity": "important",
                    "comment": "Clarify this claim.",
                    "anchor": {
                        "kind": "text-range",
                        "selector": "[mark-default='summary']",
                        "textQuote": "Quarterly summary",
                    },
                },
            },
        )
    finally:
        store.close()

    assert event["type"] == "human.annotation.created"
    assert event["actor"] == "human"
    assert event["scope"] == "default"
    assert event["payload"]["status"] == "pending"
    assert event["message_id"]
    assert "[show-annotation:default:created] question" in event["transcript_text"]
    assert "Clarify this claim." in event["transcript_text"]
    assert "Quote: Quarterly summary" in event["transcript_text"]

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        message_row = conn.execute(select(messages).where(messages.c.id == event["message_id"])).mappings().one()

    assert message_row["author"] == "user"


def test_show_event_store_records_annotation_resolution(isolated_state):
    _seed_session()

    store = ShowSessionEventStore()
    try:
        event = store.append(
            "ses_mark",
            {
                "type": "human.annotation.resolved",
                "annotation": {
                    "id": "annotation_1",
                    "comment": "This is resolved.",
                },
            },
        )
    finally:
        store.close()

    assert event["payload"]["id"] == "annotation_1"
    assert event["payload"]["status"] == "resolved"
    assert "resolved" in event["transcript_text"]


def test_show_event_store_keeps_object_ids_separate_from_event_ids(isolated_state):
    _seed_session()

    store = ShowSessionEventStore()
    try:
        created = store.append(
            "ses_mark",
            {
                "type": "assistant.mark.created",
                "mark": {
                    "id": "mark_1",
                    "target": "summary",
                    "body": "Created.",
                },
            },
        )
        resolved = store.append(
            "ses_mark",
            {
                "type": "assistant.mark.resolved",
                "mark": {
                    "id": "mark_1",
                    "target": "summary",
                    "body": "Resolved.",
                },
            },
        )
    finally:
        store.close()

    assert created["payload"]["id"] == "mark_1"
    assert resolved["payload"]["id"] == "mark_1"
    assert created["id"] != "mark_1"
    assert resolved["id"] != "mark_1"
    assert created["id"] != resolved["id"]


def test_show_event_store_records_intent_dispatch_payload(isolated_state):
    _seed_session()

    store = ShowSessionEventStore()
    try:
        event = store.append(
            "ses_mark",
            {
                "type": "human.intent.submitted",
                "payload": {
                    "component": "decision",
                    "intent": "choose",
                    "value": "B",
                    "comment": "Pick B.",
                    "dispatch": True,
                },
            },
        )
    finally:
        store.close()

    assert event["payload"]["dispatch"] is True
    assert "[show-intent:default] choose" in event["transcript_text"]
    assert "Pick B." in event["transcript_text"]


def test_show_event_store_records_assistant_page_update(isolated_state):
    _seed_session()

    store = ShowSessionEventStore()
    try:
        event = store.append(
            "ses_mark",
            {
                "type": "assistant.page.updated",
                "payload": {
                    "summary": "Updated the Show Page with the revised flow.",
                },
            },
        )
    finally:
        store.close()

    assert event["actor"] == "assistant"
    assert event["message_id"]
    assert "[show-page-updated] Updated the Show Page" in event["transcript_text"]


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


def test_show_event_store_uses_server_created_at_for_storage_cursor(monkeypatch, isolated_state):
    _seed_session()
    monkeypatch.setattr("core.show_session_events._utc_now_iso", lambda: "2026-05-30T10:00:00+00:00")

    store = ShowSessionEventStore()
    try:
        event = store.append(
            "ses_mark",
            {
                "type": "assistant.mark.created",
                "mark": {
                    "target": "summary",
                    "body": "body",
                    "createdAt": "1999-01-01T00:00:00+00:00",
                },
            },
        )
    finally:
        store.close()

    assert event["created_at"] == "2026-05-30T10:00:00+00:00"
    assert event["payload"]["createdAt"] == "1999-01-01T00:00:00+00:00"

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        event_row = conn.execute(select(show_session_events)).mappings().one()

    assert event_row["created_at"] == "2026-05-30T10:00:00+00:00"


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
