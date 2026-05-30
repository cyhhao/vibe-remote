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
from storage.models import agent_sessions, messages, scopes
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


def _seed_titled_session(conn, scope_id: str, session_id: str, title: str) -> None:
    now = messages_service._utc_now_iso()
    conn.execute(
        agent_sessions.insert().values(
            id=session_id,
            scope_id=scope_id,
            agent_backend="claude",
            agent_variant="default",
            session_anchor="anchor_" + session_id,
            native_session_id="",
            title=title,
            status="active",
            metadata_json="{}",
            created_at=now,
            updated_at=now,
            last_active_at=now,
        )
    )


def _insert_msg(conn, scope_id, session_id, author, text, created_at, *, read=True):
    """Direct insert so the test controls created_at (second-resolution) + read_at."""
    conn.execute(
        messages.insert().values(
            id=f"msg_{session_id}_{created_at[-9:]}_{author}",
            scope_id=scope_id,
            session_id=session_id,
            platform="avibe",
            author=author,
            type="user" if author == "user" else "assistant",
            content_text=text,
            content_json="{}",
            metadata_json="{}",
            created_at=created_at,
            updated_at=created_at,
            read_at=created_at if (read and author == "agent") else None,
        )
    )


def test_list_inbox_sessions_per_session_feed(isolated_state):
    """One card per session, sorted by last activity (any author) desc, preview =
    latest agent reply, replied = last message is the user's, with unread counts."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        conn.execute(scopes.update().where(scopes.c.id == scope_id).values(display_name="My Project"))
        _seed_titled_session(conn, scope_id, "ses_a", "Alpha")
        _seed_titled_session(conn, scope_id, "ses_b", "Beta")
        _seed_titled_session(conn, scope_id, "ses_c", "Gamma")
        # ses_a: agent reply (read), then the user replied last → replied, no unread.
        _insert_msg(conn, scope_id, "ses_a", "agent", "A1", "2026-05-30T10:00:00Z")
        _insert_msg(conn, scope_id, "ses_a", "user", "AU", "2026-05-30T10:05:00Z")
        # ses_b: two agent replies, the second unread → most recent activity, unread=1.
        _insert_msg(conn, scope_id, "ses_b", "agent", "B1", "2026-05-30T10:01:00Z")
        _insert_msg(conn, scope_id, "ses_b", "agent", "B2", "2026-05-30T10:10:00Z", read=False)
        # ses_c: only a user message, no agent reply → excluded from the feed.
        _insert_msg(conn, scope_id, "ses_c", "user", "CU", "2026-05-30T10:20:00Z")

    with engine.connect() as conn:
        feed = messages_service.list_inbox_sessions(conn, platform="avibe")

    rows = feed["sessions"]
    # ses_c excluded (no agent reply); ses_b before ses_a (10:10 > 10:05).
    assert [r["session_id"] for r in rows] == ["ses_b", "ses_a"]

    b, a = rows[0], rows[1]
    assert b["title"] == "Beta" and b["project_name"] == "My Project" and b["project_id"] == "proj_test"
    assert b["preview_text"] == "B2" and b["unread_count"] == 1 and b["unread"] is True
    assert b["replied"] is False  # last message is the agent's
    # ses_a: preview is the latest AGENT reply (A1), not the user's last message.
    assert a["preview_text"] == "A1" and a["unread_count"] == 0
    assert a["replied"] is True  # last message is the user's

    # Unread filter drops the fully-read ses_a.
    with engine.connect() as conn:
        unread_feed = messages_service.list_inbox_sessions(conn, platform="avibe", unread_only=True)
    assert [r["session_id"] for r in unread_feed["sessions"]] == ["ses_b"]


def test_list_inbox_sessions_pagination(isolated_state):
    """Keyset 'load more' walks sessions in last-activity order."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        for i in range(3):
            sid = f"ses_{i}"
            _seed_titled_session(conn, scope_id, sid, f"S{i}")
            _insert_msg(conn, scope_id, sid, "agent", f"reply {i}", f"2026-05-30T1{i}:00:00Z")

    with engine.connect() as conn:
        page1 = messages_service.list_inbox_sessions(conn, platform="avibe", limit=2)
        assert [r["session_id"] for r in page1["sessions"]] == ["ses_2", "ses_1"]
        assert page1["next_cursor"]
        page2 = messages_service.list_inbox_sessions(
            conn, platform="avibe", limit=2, before=page1["next_cursor"]
        )
    assert [r["session_id"] for r in page2["sessions"]] == ["ses_0"]
