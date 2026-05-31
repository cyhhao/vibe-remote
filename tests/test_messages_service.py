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


def test_list_session_messages_filters_to_user_facing_types(isolated_state):
    """The chat transcript scopes to user-facing types so the intermediate
    assistant / tool_call / notify rows now persisted for avibe stay out of the
    dialogue view (they're the process log, not the conversation)."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_tx")
        # Distinct timestamps so chronological order is deterministic (append's
        # second-resolution now would tie and fall back to random id order).
        _insert_msg(conn, scope_id, "ses_tx", "user", "q", "2026-05-30T10:00:00Z", msg_type="user")
        _insert_msg(conn, scope_id, "ses_tx", "agent", "thinking", "2026-05-30T10:00:01Z", msg_type="assistant")
        _insert_msg(conn, scope_id, "ses_tx", "agent", "ran tool", "2026-05-30T10:00:02Z", msg_type="tool_call")
        _insert_msg(conn, scope_id, "ses_tx", "agent", "progress", "2026-05-30T10:00:03Z", msg_type="notify")
        _insert_msg(conn, scope_id, "ses_tx", "agent", "final", "2026-05-30T10:00:04Z", msg_type="result")

    with engine.connect() as conn:
        every = messages_service.list_session_messages(conn, session_id="ses_tx")
        dialogue = messages_service.list_session_messages(
            conn, session_id="ses_tx", types=("user", "result")
        )

    assert [m["type"] for m in every["messages"]] == ["user", "assistant", "tool_call", "notify", "result"]
    assert [m["text"] for m in dialogue["messages"]] == ["q", "final"]


def test_same_second_messages_order_by_insertion(isolated_state):
    """Rows sharing a (second-resolution) created_at still order by insertion in
    the transcript: the monotonic message id breaks the ``(created_at, id)`` tie,
    so a fast avibe turn never renders the agent result before the user prompt
    (nor lets the inbox pick the wrong 'last' row)."""
    engine = create_sqlite_engine()
    fixed = "2026-05-30T12:00:00Z"
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_fast")
        # Identical created_at for both rows; ids come from _new_message_id() in
        # insertion order (the DB round-trip between calls separates microseconds).
        for author, mtype, text in (("user", "user", "prompt"), ("agent", "result", "answer")):
            conn.execute(
                messages.insert().values(
                    id=messages_service._new_message_id(),
                    scope_id=scope_id,
                    session_id="ses_fast",
                    platform="avibe",
                    author=author,
                    type=mtype,
                    content_text=text,
                    content_json="{}",
                    metadata_json="{}",
                    created_at=fixed,
                    updated_at=fixed,
                    read_at=None,
                )
            )

    with engine.connect() as conn:
        page = messages_service.list_session_messages(conn, session_id="ses_fast")
    assert [m["text"] for m in page["messages"]] == ["prompt", "answer"]


def test_list_session_messages_keeps_show_page_marks(isolated_state):
    """Show-Page transcript marks (author='agent' → type='assistant', but
    metadata.source='show_page') stay visible in the chat transcript even though
    plain intermediate 'assistant' process rows are filtered out."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_mark")
        messages_service.append(
            conn, scope_id=scope_id, session_id="ses_mark", platform="avibe", author="user", text="q"
        )
        # Avibe intermediate assistant (process log) — must be hidden.
        messages_service.append(
            conn, scope_id=scope_id, session_id="ses_mark", platform="avibe",
            author="agent", message_type="assistant", text="thinking",
        )
        # Show-page assistant mark — must stay visible via metadata.source.
        messages_service.append(
            conn, scope_id=scope_id, session_id="ses_mark", platform="avibe",
            author="agent", text="annotation", metadata={"source": "show_page"},
        )
        messages_service.append(
            conn, scope_id=scope_id, session_id="ses_mark", platform="avibe",
            author="agent", message_type="result", text="final",
        )

    with engine.connect() as conn:
        page = messages_service.list_session_messages(
            conn, session_id="ses_mark", types=("user", "result"), include_metadata_sources=("show_page",)
        )
    texts = [m["text"] for m in page["messages"]]
    assert texts == ["q", "annotation", "final"]  # 'thinking' (plain assistant) filtered out


def test_transcript_keeps_notify_terminal_marker(isolated_state):
    """The chat transcript keeps a terminal ``notify`` (e.g. an agent run that
    failed and stopped without a result) while still hiding the intermediate
    assistant / tool_call process rows."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_n")
        for author, mtype, text in (
            ("user", "user", "go"),
            ("agent", "assistant", "thinking"),
            ("agent", "tool_call", "ran tool"),
            ("agent", "notify", "Agent run failed and stopped."),
        ):
            messages_service.append(
                conn, scope_id=scope_id, session_id="ses_n", platform="avibe",
                author=author, message_type=mtype, text=text,
            )

    with engine.connect() as conn:
        page = messages_service.list_session_messages(
            conn, session_id="ses_n", types=("user", "result", "notify"), include_metadata_sources=("show_page",)
        )
    texts = [m["text"] for m in page["messages"]]
    assert texts == ["go", "Agent run failed and stopped."]  # notify kept; assistant/tool_call hidden


def test_append_defaults_type_from_author(isolated_state):
    """Callers that omit message_type (e.g. show-page transcript annotations)
    get a type derived from author — a human row must be 'user' so the
    user+result transcript filter keeps it, not mis-typed 'assistant'."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_def")
        user_row = messages_service.append(
            conn, scope_id=scope_id, session_id="ses_def", platform="avibe", author="user", text="hi"
        )
        agent_row = messages_service.append(
            conn, scope_id=scope_id, session_id="ses_def", platform="avibe", author="agent", text="yo"
        )
    assert user_row["type"] == "user"
    assert agent_row["type"] == "assistant"


def test_unread_counts_by_session_splits_within_a_scope(isolated_state):
    """Two sessions in one project report distinct per-session unread counts,
    counting unread agent *result* messages only. Intermediate assistant /
    tool_call rows (persisted for avibe but not user-facing) must NOT inflate
    the badge past what the inbox card shows."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_a")
        _seed_session(conn, scope_id, "ses_b")
        for _ in range(2):
            messages_service.append(
                conn, scope_id=scope_id, session_id="ses_a", platform="avibe",
                author="agent", message_type="result", text="a",
            )
        messages_service.append(
            conn, scope_id=scope_id, session_id="ses_b", platform="avibe",
            author="agent", message_type="result", text="b",
        )
        # An unread assistant + tool_call (intermediate) and a user message
        # must NOT count toward the unread badge.
        messages_service.append(
            conn, scope_id=scope_id, session_id="ses_b", platform="avibe",
            author="agent", message_type="assistant", text="thinking",
        )
        messages_service.append(
            conn, scope_id=scope_id, session_id="ses_b", platform="avibe",
            author="agent", message_type="tool_call", text="ran tool",
        )
        messages_service.append(
            conn, scope_id=scope_id, session_id="ses_b", platform="avibe",
            author="user", message_type="user", text="hi",
        )

    with engine.connect() as conn:
        by_session = messages_service.unread_counts_by_session(conn, platform="avibe")
        by_scope = messages_service.unread_counts(conn, platform="avibe")

    assert by_session == {"ses_a": 2, "ses_b": 1}
    # Scope aggregate still lumps both sessions together (result-only).
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


def _insert_msg(conn, scope_id, session_id, author, text, created_at, *, read=True, msg_type=None):
    """Direct insert so the test controls created_at (second-resolution) + read_at.

    Agent rows default to type='result' (the user-facing reply the inbox
    previews); pass ``msg_type`` to insert an intermediate type (assistant /
    tool_call) that must NOT drive the inbox preview.
    """
    resolved_type = msg_type or ("user" if author == "user" else "result")
    conn.execute(
        messages.insert().values(
            id=f"msg_{session_id}_{created_at[-9:]}_{author}_{resolved_type}",
            scope_id=scope_id,
            session_id=session_id,
            platform="avibe",
            author=author,
            type=resolved_type,
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
        # ses_b: two result replies, the second unread → most recent activity, unread=1.
        _insert_msg(conn, scope_id, "ses_b", "agent", "B1", "2026-05-30T10:01:00Z")
        _insert_msg(conn, scope_id, "ses_b", "agent", "B2", "2026-05-30T10:10:00Z", read=False)
        # An intermediate assistant message arrives LAST — it must bump the
        # activity clock (sort key) but NOT become the preview (preview = result).
        _insert_msg(
            conn, scope_id, "ses_b", "agent", "thinking…", "2026-05-30T10:11:00Z",
            read=False, msg_type="assistant",
        )
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

    # The sidebar badge map agrees with the feed cards' result-only unread_count
    # — the unread intermediate 'thinking' assistant row at 10:11 must NOT make
    # the two sources disagree (1 vs 2).
    with engine.connect() as conn:
        by_session = messages_service.unread_counts_by_session(conn, platform="avibe")
    assert by_session == {"ses_b": 1}
    assert b["unread_count"] == by_session["ses_b"]


def test_list_inbox_sessions_includes_notify_only_failed_turn(isolated_state):
    """A turn that fails before producing any ``result`` persists only a terminal
    ``notify``; that failed conversation must still surface in the inbox (with the
    error as preview) instead of vanishing once the user leaves the Chat page."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_titled_session(conn, scope_id, "ses_fail", "Failed")
        _insert_msg(conn, scope_id, "ses_fail", "user", "do the thing", "2026-05-30T10:00:00Z")
        # No ``result`` ever lands — only a terminal failure notify.
        _insert_msg(
            conn, scope_id, "ses_fail", "agent", "❌ Claude error: boom",
            "2026-05-30T10:00:05Z", read=False, msg_type="notify",
        )

    with engine.connect() as conn:
        rows = messages_service.list_inbox_sessions(conn, platform="avibe")["sessions"]

    assert [r["session_id"] for r in rows] == ["ses_fail"]
    row = rows[0]
    # Preview is the failure notify so the user sees WHY the turn ended.
    assert row["preview_text"] == "❌ Claude error: boom"
    # ``replied`` reflects who spoke last (the agent's notify), not the user.
    assert row["replied"] is False


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


# --- Send-while-busy queue + per-session draft ------------------------------


def test_enqueue_list_and_pop_queued(isolated_state):
    """Queued messages persist in order, stay OUT of the conversation transcript
    (different ``type``), and ``pop_queued`` reads-then-deletes them atomically."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_q")
        messages_service.enqueue_queued(conn, scope_id=scope_id, session_id="ses_q", text="first")
        time.sleep(0.001)
        messages_service.enqueue_queued(conn, scope_id=scope_id, session_id="ses_q", text="second")

    with engine.connect() as conn:
        queued = messages_service.list_queued(conn, "ses_q")
        # Queued rows never appear in the user/result/notify transcript.
        transcript = messages_service.list_session_messages(
            conn, session_id="ses_q", types=("user", "result", "notify")
        )
    assert [q["text"] for q in queued] == ["first", "second"]
    assert all(q["type"] == "queued" for q in queued)
    assert transcript["messages"] == []

    # pop returns them in order and clears the queue.
    with engine.begin() as conn:
        popped = messages_service.pop_queued(conn, "ses_q")
    assert [p["text"] for p in popped] == ["first", "second"]
    with engine.connect() as conn:
        assert messages_service.list_queued(conn, "ses_q") == []


def test_remove_queued_targets_only_queued(isolated_state):
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_rm")
        a = messages_service.enqueue_queued(conn, scope_id=scope_id, session_id="ses_rm", text="a")
        messages_service.enqueue_queued(conn, scope_id=scope_id, session_id="ses_rm", text="b")
        # A real user message must NOT be removable through remove_queued.
        user_row = messages_service.append(
            conn, scope_id=scope_id, session_id="ses_rm", platform="avibe", author="user", text="real"
        )

    with engine.begin() as conn:
        assert messages_service.remove_queued(conn, "ses_rm", a["id"]) is True
        # Wrong session id must NOT delete the row (scoped delete).
        assert messages_service.remove_queued(conn, "ses_other", a["id"]) is False
        # A real user message is not removable through remove_queued.
        assert messages_service.remove_queued(conn, "ses_rm", user_row["id"]) is False
    with engine.connect() as conn:
        assert [q["text"] for q in messages_service.list_queued(conn, "ses_rm")] == ["b"]


def test_draft_upsert_get_and_clear(isolated_state):
    """A session keeps exactly one draft row; setting replaces it, blank clears,
    and the draft never shows in the transcript."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_d")
        messages_service.set_draft(conn, scope_id=scope_id, session_id="ses_d", text="half typed")

    with engine.connect() as conn:
        draft = messages_service.get_draft(conn, "ses_d")
    assert draft is not None and draft["text"] == "half typed" and draft["type"] == "draft"

    # Setting again replaces in place (still exactly one draft row).
    with engine.begin() as conn:
        messages_service.set_draft(conn, scope_id=scope_id, session_id="ses_d", text="rewritten")
    with engine.connect() as conn:
        rows = conn.execute(
            select(messages).where(messages.c.session_id == "ses_d", messages.c.type == "draft")
        ).all()
        draft = messages_service.get_draft(conn, "ses_d")
    assert len(rows) == 1 and draft["text"] == "rewritten"

    # Blank text clears the draft.
    with engine.begin() as conn:
        assert messages_service.set_draft(conn, scope_id=scope_id, session_id="ses_d", text="   ") is None
    with engine.connect() as conn:
        assert messages_service.get_draft(conn, "ses_d") is None

    # clear_draft is idempotent.
    with engine.begin() as conn:
        messages_service.set_draft(conn, scope_id=scope_id, session_id="ses_d", text="again")
    with engine.begin() as conn:
        messages_service.clear_draft(conn, "ses_d")
    with engine.connect() as conn:
        assert messages_service.get_draft(conn, "ses_d") is None


def test_inbox_ignores_draft_and_queued_activity(isolated_state):
    """A saved draft / pending queued message lives in the messages table but
    must NOT bump the session in the inbox or flip its 'replied' badge — only
    sent conversation counts as activity (Codex P2)."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_inbox")
        _insert_msg(conn, scope_id, "ses_inbox", "user", "hi", "2026-05-30T10:00:00Z")
        _insert_msg(conn, scope_id, "ses_inbox", "agent", "reply", "2026-05-30T10:00:01Z")
        # A LATER draft + queued (newer created_at) must not count as activity.
        _insert_msg(conn, scope_id, "ses_inbox", "user", "typing", "2026-05-30T10:05:00Z", msg_type="draft")
        _insert_msg(conn, scope_id, "ses_inbox", "user", "queued", "2026-05-30T10:06:00Z", msg_type="queued")

    with engine.connect() as conn:
        rows = messages_service.list_inbox_sessions(conn, platform="avibe")["sessions"]
    assert len(rows) == 1
    row = rows[0]
    # Activity clock = the agent reply, NOT the later draft/queued rows.
    assert row["last_activity_at"] == "2026-05-30T10:00:01Z"
    assert row["last_message_author"] == "agent"
    assert row["replied"] is False


def test_list_session_messages_tail_returns_recent_window(isolated_state):
    """``tail=True`` returns the most-recent ``limit`` rows in chronological
    order (not the oldest page), so the Chat page's gap recovery sees the latest
    messages even in a long session."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_scope(conn)
        _seed_session(conn, scope_id, "ses_tail")
        for i in range(5):
            _insert_msg(conn, scope_id, "ses_tail", "user", f"m{i}", f"2026-05-30T10:0{i}:00Z")

    with engine.connect() as conn:
        oldest = messages_service.list_session_messages(conn, session_id="ses_tail", limit=3)
        recent = messages_service.list_session_messages(conn, session_id="ses_tail", limit=3, tail=True)
    # Default page = oldest 3; tail = newest 3, still chronological.
    assert [m["text"] for m in oldest["messages"]] == ["m0", "m1", "m2"]
    assert [m["text"] for m in recent["messages"]] == ["m2", "m3", "m4"]
    assert recent["next_after_id"] is None
