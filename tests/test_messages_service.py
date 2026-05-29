"""Focused tests for storage.messages_service behaviours that are easy to
regress: pagination cursor and the ``mark_session_read`` boundary check.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import messages_service
from storage.db import create_sqlite_engine
from storage.importer import ensure_sqlite_state
from storage.models import agent_sessions, messages
from storage.settings_service import upsert_scope


@pytest.fixture()
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    yield tmp_path


def _seed_scope(conn) -> str:
    now = messages_service._utc_now_iso()
    return upsert_scope(conn, platform="avibe", scope_type="project", native_id="proj_test", now=now)


def _seed_session(conn, scope_id: str, session_id: str) -> None:
    now = messages_service._utc_now_iso()
    conn.execute(
        agent_sessions.insert().values(
            id=session_id,
            scope_id=scope_id,
            agent_backend="claude",
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


def test_mark_session_read_ties_break_on_id(isolated_state):
    """When ``until_message_id`` points at a message whose ``created_at``
    is shared by newer messages (second precision), only rows at-or-before
    the anchor *by id* should be marked read. Otherwise the user's still-
    unread newest reply gets cleared.
    """
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_test")
        # Use a fixed timestamp so all three rows share the same created_at
        # (mimicking the second-precision collision).
        fixed_now = "2026-05-26T13:00:00Z"
        for content in ("first", "second", "third"):
            payload = {
                "id": messages_service._new_message_id(),
                "scope_id": scope_id,
                "session_id": "ses_test",
                "platform": "avibe",
                "author": "agent",
                "author_id": None,
                "author_name": None,
                "native_message_id": None,
                "parent_native_message_id": None,
                "content_text": content,
                "content_json": "{}",
                "metadata_json": "{}",
                "created_at": fixed_now,
                "updated_at": fixed_now,
                "delivered_at": None,
                "read_at": None,
            }
            conn.execute(messages.insert().values(**payload))

    with engine.connect() as conn:
        ordered = conn.execute(
            select(messages.c.id, messages.c.content_text)
            .where(messages.c.session_id == "ses_test")
            .order_by(messages.c.id.asc())
        ).all()
        # Take the middle row as the anchor: its lexicographically-smaller
        # id puts "first" before it and one row after it.
        anchor_id = ordered[1][0]
        anchor_text = ordered[1][1]

    with engine.begin() as conn:
        updated = messages_service.mark_session_read(conn, "ses_test", until_message_id=anchor_id)

    assert updated == 2, "should mark only the anchor + the row with smaller id"

    with engine.connect() as conn:
        rows = conn.execute(
            select(messages.c.content_text, messages.c.read_at)
            .where(messages.c.session_id == "ses_test")
            .order_by(messages.c.id.asc())
        ).all()
    read_states = {text: (read_at is not None) for text, read_at in rows}
    # The two rows up to and including the anchor are marked read…
    assert read_states[anchor_text] is True
    # …and the row with the larger id (same timestamp) stays unread.
    unread = [text for text, read in read_states.items() if not read]
    assert len(unread) == 1, "exactly one row after the anchor must remain unread"


def test_mark_session_read_without_anchor_marks_all(isolated_state):
    """No ``until_message_id`` → mark every unread agent row."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_all")
        for _ in range(3):
            messages_service.append(
                conn,
                scope_id=scope_id,
                session_id="ses_all",
                platform="avibe",
                author="agent",
                text="payload",
            )
            time.sleep(0.001)

    with engine.begin() as conn:
        updated = messages_service.mark_session_read(conn, "ses_all")
    assert updated == 3


def test_list_session_messages_cursor_uses_clamped_limit(isolated_state):
    """Regression: callers that pass ``limit > 500`` must still get a
    cursor when the result is a full clamped page, so they can paginate
    past the 500 mark instead of silently truncating at the cap.
    """
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_page")
        # 501 rows so the clamped 500-row page returns full and a
        # follow-up cursor is needed.
        for _ in range(501):
            messages_service.append(
                conn,
                scope_id=scope_id,
                session_id="ses_page",
                platform="avibe",
                author="agent",
                text="row",
            )

    with engine.connect() as conn:
        page = messages_service.list_session_messages(conn, session_id="ses_page", limit=1000)
    # Pre-fix this returned ``next_after_id=None`` even though there were
    # 501 rows total. The clamp-aware fix emits a cursor.
    assert len(page["messages"]) == 500
    assert page["next_after_id"] is not None


def test_list_inbox_cursor_uses_clamped_limit(isolated_state):
    """Same regression as the per-session pagination, for the Inbox feed."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_inbox")
        for _ in range(201):
            messages_service.append(
                conn,
                scope_id=scope_id,
                session_id="ses_inbox",
                platform="avibe",
                author="agent",
                text="ping",
            )

    with engine.connect() as conn:
        page = messages_service.list_inbox(conn, platform="avibe", limit=1000)
    assert len(page["messages"]) == 200
    assert page["next_before_id"] is not None


def test_unread_counts_by_session_splits_within_a_scope(isolated_state):
    """Two sessions in one project must report distinct per-session unread
    counts, even though the scope-level aggregate lumps them together."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_a")
        _seed_session(conn, scope_id, "ses_b")
        for _ in range(2):
            messages_service.append(
                conn, scope_id=scope_id, session_id="ses_a", platform="avibe", author="agent", text="a"
            )
        messages_service.append(
            conn, scope_id=scope_id, session_id="ses_b", platform="avibe", author="agent", text="b"
        )
        # A user message and a read agent message must not count.
        messages_service.append(
            conn, scope_id=scope_id, session_id="ses_b", platform="avibe", author="user", text="hi"
        )

    with engine.connect() as conn:
        by_session = messages_service.unread_counts_by_session(conn, platform="avibe")
        by_scope = messages_service.unread_counts(conn, platform="avibe")

    assert by_session == {"ses_a": 2, "ses_b": 1}
    # Scope aggregate still lumps both sessions together.
    assert by_scope == {scope_id: 3}
