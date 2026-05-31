"""CRUD over the platform-agnostic ``messages`` table.

The workbench Inbox + per-session history both read through this
module so they get a consistent shape regardless of which platform
originated the row. ``append`` is the canonical write path —
adapters and REST routes call it instead of touching the table
directly so future invariants (e.g. SSE fan-out hooks, audit logging)
land in one place.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.engine import Connection

from storage.models import agent_sessions, messages, scopes


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_message_id() -> str:
    """Time-sortable message id.

    The transcript and inbox order rows by ``(created_at, id)`` and
    ``created_at`` is second-resolution, so two rows written in the same second
    — e.g. a fast avibe turn where the user prompt and the agent result land
    together — tie on ``created_at``. A microsecond-clock prefix makes the id
    monotonic so that tie-break preserves insertion order; otherwise a random
    uuid could render the result before the prompt, or make the inbox pick the
    wrong "last" row for its activity / replied state. The random suffix keeps
    ids unique within the same microsecond.
    """
    return f"msg_{int(time.time() * 1_000_000):015x}{uuid.uuid4().hex[:8]}"


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    try:
        content = json.loads(row.get("content_json") or "{}")
    except json.JSONDecodeError:
        content = {}
    try:
        metadata = json.loads(row.get("metadata_json") or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return {
        "id": row["id"],
        "scope_id": row.get("scope_id"),
        "session_id": row.get("session_id"),
        "platform": row.get("platform"),
        "author": row.get("author"),
        "type": row.get("type"),
        "author_id": row.get("author_id"),
        "author_name": row.get("author_name"),
        "source": row.get("source"),
        "native_message_id": row.get("native_message_id"),
        "parent_native_message_id": row.get("parent_native_message_id"),
        "text": row.get("content_text") or content.get("text") or "",
        "content": content,
        "metadata": metadata,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "delivered_at": row.get("delivered_at"),
        "read_at": row.get("read_at"),
    }


def append(
    conn: Connection,
    *,
    scope_id: str,
    session_id: Optional[str],
    platform: str,
    author: str,
    message_type: Optional[str] = None,
    text: Optional[str] = None,
    content: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    author_id: Optional[str] = None,
    author_name: Optional[str] = None,
    source: Optional[str] = None,
    native_message_id: Optional[str] = None,
    parent_native_message_id: Optional[str] = None,
    delivered_at: Optional[str] = None,
    read_at: Optional[str] = None,
) -> dict[str, Any]:
    """Insert a new message row and return its payload.

    ``content`` is the rich blob (text + attachments + tool_calls); if
    ``text`` is omitted we project ``content['text']`` into
    ``content_text`` so plain-text search keeps working.
    """

    body: dict[str, Any] = {}
    if content:
        body.update(content)
    if text is not None:
        body.setdefault("text", text)
    plain = text if text is not None else body.get("text") or None

    # Default the type from the author so legacy callers that only set ``author``
    # (e.g. show-page transcript annotations) stay correctly typed — a human row
    # must be ``user`` (not ``assistant``), or the user+result transcript filter
    # would drop it. Typed callers (inbox/IM mirror) pass message_type explicitly.
    resolved_type = message_type or ("user" if author == "user" else "assistant")

    now = _utc_now_iso()
    payload = {
        "id": _new_message_id(),
        "scope_id": scope_id,
        "session_id": session_id,
        "platform": platform,
        "author": author,
        "type": resolved_type,
        "author_id": author_id,
        "author_name": author_name,
        "source": source,
        "native_message_id": native_message_id,
        "parent_native_message_id": parent_native_message_id,
        "content_text": plain,
        "content_json": json.dumps(body),
        "metadata_json": json.dumps(metadata or {}),
        "created_at": now,
        "updated_at": now,
        "delivered_at": delivered_at,
        "read_at": read_at,
    }
    conn.execute(messages.insert().values(**payload))
    return _row_to_payload(payload)


def list_session_messages(
    conn: Connection,
    *,
    session_id: str,
    after_id: Optional[str] = None,
    limit: int = 50,
    types: Optional[Iterable[str]] = None,
    include_metadata_sources: Iterable[str] = (),
    tail: bool = False,
) -> dict[str, Any]:
    """Return messages for one session in chronological order with cursor pagination.

    ``types`` optionally restricts the rows to a set of message types. The chat
    transcript passes ``('user', 'result')`` so the intermediate ``assistant`` /
    ``tool_call`` / ``notify`` rows — now persisted for avibe sessions too — stay
    out of the conversation view (they're the process log, not the dialogue).

    ``include_metadata_sources`` keeps rows whose ``metadata.source`` matches even
    when their type is filtered out — the chat transcript passes ``('show_page',)``
    so Show-Page transcript marks (written with ``author='agent'`` → ``type
    ='assistant'``) stay visible alongside the user/result dialogue.

    ``tail`` returns the most-recent ``limit`` rows (still chronological) instead
    of the oldest page — used by the Chat page's reconnect/visibility gap
    recovery, which needs the RECENT window (a long chat's oldest page would
    never surface a missed latest prompt/reply). ``tail`` ignores ``after_id``
    and returns no cursor.
    """

    query = select(messages).where(messages.c.session_id == session_id)
    metadata_sources = list(include_metadata_sources)
    if types is not None:
        type_filter = messages.c.type.in_(list(types))
        if metadata_sources:
            query = query.where(
                or_(type_filter, func.json_extract(messages.c.metadata_json, "$.source").in_(metadata_sources))
            )
        else:
            query = query.where(type_filter)
    effective_limit = min(max(int(limit), 1), 500)
    if tail:
        # Newest ``limit`` rows, then flip back to chronological for the caller.
        query = query.order_by(messages.c.created_at.desc(), messages.c.id.desc()).limit(effective_limit)
        rows = [_row_to_payload(dict(row)) for row in conn.execute(query).mappings().all()]
        rows.reverse()
        return {"messages": rows, "next_after_id": None}
    if after_id:
        anchor = conn.execute(
            select(messages.c.created_at).where(messages.c.id == after_id)
        ).scalar_one_or_none()
        if anchor is not None:
            query = query.where(
                or_(
                    messages.c.created_at > anchor,
                    and_(messages.c.created_at == anchor, messages.c.id > after_id),
                )
            )
    query = query.order_by(messages.c.created_at.asc(), messages.c.id.asc()).limit(effective_limit)
    rows = [_row_to_payload(dict(row)) for row in conn.execute(query).mappings().all()]
    # Compare against the clamped page size; a caller requesting > 500
    # would otherwise receive a full 500-row page with a null cursor and
    # silently stop paginating.
    next_after = rows[-1]["id"] if len(rows) == effective_limit else None
    return {"messages": rows, "next_after_id": next_after}


# --- Send-while-busy queue + per-session draft -----------------------------
# Both reuse the ``messages`` table via dedicated ``type`` values so no extra
# table is needed (the queue is ephemeral operational state, not conversation):
#   type='queued' — a message the user sent while a turn was in flight; flushed
#                   (merged, in order) into one dispatch when the turn ends.
#   type='draft'  — the user's unsent compose text for a session; one row per
#                   session, persisted so switching sessions/devices keeps it.
# Both carry author='user'; the transcript (user/result/notify), inbox and
# unread queries are all type-filtered, so neither leaks into the conversation.

QUEUED_TYPE = "queued"
DRAFT_TYPE = "draft"
# A reserved-but-not-yet-accepted user row: persisted BEFORE dispatch (so it
# reserves its (created_at, id) for correct ordering) but hidden from the
# transcript, the queue AND the inbox until the controller decides whether the
# turn started (→ promote to 'user') or must be queued (→ promote to 'queued').
# This stops another tab from briefly seeing the row as a sent prompt during the
# dispatch window (Codex P2).
PENDING_TYPE = "pending"
# Ephemeral types that must never count as inbox activity / conversation.
NON_CONVERSATION_TYPES = (QUEUED_TYPE, DRAFT_TYPE, PENDING_TYPE)

# The transcript-visible types — the SINGLE source of truth shared by the
# history fetch (``list_session_messages``) AND the live ``message.new`` publish
# gate, so what a page loads and what it receives over the stream are identical.
# Excludes the agent's process log (``assistant`` / ``tool_call``) and ``system``
# (which isn't persisted at all). Harness-triggered prompts are ``user``, so they
# are included. ``show_page`` transcript marks are kept via a metadata-source
# override in the fetch even though their row type is ``assistant``.
TRANSCRIPT_TYPES = ("user", "result", "notify")


def enqueue_queued(
    conn: Connection,
    *,
    scope_id: str,
    session_id: str,
    text: str,
    author_id: Optional[str] = None,
    author_name: Optional[str] = None,
) -> dict[str, Any]:
    """Append a queued ('send while busy') message for a session."""
    return append(
        conn,
        scope_id=scope_id,
        session_id=session_id,
        platform="avibe",
        author="user",
        source="user",
        message_type=QUEUED_TYPE,
        text=text,
        author_id=author_id,
        author_name=author_name,
    )


def list_queued(conn: Connection, session_id: str) -> list[dict[str, Any]]:
    """Pending queued messages for a session, oldest first."""
    query = (
        select(messages)
        .where(messages.c.session_id == session_id)
        .where(messages.c.type == QUEUED_TYPE)
        .order_by(messages.c.created_at.asc(), messages.c.id.asc())
    )
    return [_row_to_payload(dict(row)) for row in conn.execute(query).mappings().all()]


def pop_queued(conn: Connection, session_id: str) -> list[dict[str, Any]]:
    """Claim the session's queued messages: read them (oldest first), then delete
    them in the SAME transaction, so the rows are returned exactly once. Empty
    list when the queue is empty.

    Select-then-delete rather than ``DELETE ... RETURNING``: RETURNING needs
    SQLite >= 3.35, which the project does not pin, so on an older libsqlite the
    flush would raise — ``_flush_queue`` returns False and the send-while-busy
    queue never dispatches, stranding the user's queued follow-up (Codex P2).

    The DELETE is scoped to the CLAIMED row ids (not a broad session+type
    predicate): the UI server is a SEPARATE writer that can promote a just-sent
    prompt to ``queued`` between the SELECT and the DELETE, and a broad delete
    would drop that newer row without returning it — losing the user's message.
    Deleting only the ids we actually read leaves any concurrently-enqueued row
    for the next flush (Codex P2).
    """
    rows_q = (
        select(messages)
        .where(messages.c.session_id == session_id)
        .where(messages.c.type == QUEUED_TYPE)
        .order_by(messages.c.created_at.asc(), messages.c.id.asc())
    )
    rows = [_row_to_payload(dict(row)) for row in conn.execute(rows_q).mappings().all()]
    if not rows:
        return []
    claimed_ids = [r["id"] for r in rows]
    conn.execute(delete(messages).where(messages.c.id.in_(claimed_ids)))
    return rows


def promote_pending(conn: Connection, message_id: str, to_type: str) -> bool:
    """Promote a reserved ``pending`` row to its decided type — ``user`` once the
    turn is accepted, or ``queued`` when a turn is already running. The row is
    persisted as ``pending`` BEFORE dispatch (reserving its (created_at, id) for
    correct ordering) and stays hidden until this promotes it, so no other tab
    can briefly see it as a sent prompt during the dispatch window. Returns True
    if a pending row was promoted.
    """
    result = conn.execute(
        update(messages)
        .where(messages.c.id == message_id)
        .where(messages.c.type == PENDING_TYPE)
        .values(type=to_type)
    )
    return bool(result.rowcount)


def remove_queued(conn: Connection, session_id: str, message_id: str) -> bool:
    """Delete one queued message, scoped to its session so a stale / cross-session
    id can't drop another chat's queued row. Returns True if a row was removed."""
    result = conn.execute(
        delete(messages)
        .where(messages.c.id == message_id)
        .where(messages.c.session_id == session_id)
        .where(messages.c.type == QUEUED_TYPE)
    )
    return bool(result.rowcount)


def get_draft(conn: Connection, session_id: str) -> Optional[dict[str, Any]]:
    """The session's current unsent draft, or None."""
    query = (
        select(messages)
        .where(messages.c.session_id == session_id)
        .where(messages.c.type == DRAFT_TYPE)
        .order_by(messages.c.created_at.desc(), messages.c.id.desc())
        .limit(1)
    )
    row = conn.execute(query).mappings().first()
    return _row_to_payload(dict(row)) if row else None


def set_draft(conn: Connection, *, scope_id: str, session_id: str, text: Optional[str]) -> Optional[dict[str, Any]]:
    """Upsert the session's draft (one row per session). Blank text clears it."""
    conn.execute(
        delete(messages).where(messages.c.session_id == session_id).where(messages.c.type == DRAFT_TYPE)
    )
    if not text or not text.strip():
        return None
    return append(
        conn,
        scope_id=scope_id,
        session_id=session_id,
        platform="avibe",
        author="user",
        source="user",
        message_type=DRAFT_TYPE,
        text=text,
    )


def clear_draft(conn: Connection, session_id: str) -> None:
    """Drop the session's draft (e.g. after a successful send)."""
    conn.execute(
        delete(messages).where(messages.c.session_id == session_id).where(messages.c.type == DRAFT_TYPE)
    )


def unread_counts(
    conn: Connection,
    *,
    platform: Optional[str] = None,
) -> dict[str, int]:
    """Return ``{scope_id: count}`` for unread agent ``result`` messages.

    Used by the sidebar / hover popover to show per-session unread dots
    plus the global count without dragging every row through Python.
    Filtered to ``type='result'`` so it agrees with the inbox feed's UNREAD
    count, which is also result-only — otherwise intermediate ``assistant`` /
    ``tool_call`` rows (now persisted for avibe too) would inflate the badge
    past what the feed shows. (Inbox *eligibility* and *preview* also accept a
    terminal ``notify`` so failed turns stay visible, but a failure notify is
    not an unread reply — it never bumps this badge.)
    """

    query = (
        select(messages.c.scope_id, func.count(messages.c.id))
        .where(messages.c.author == "agent")
        .where(messages.c.type == "result")
        .where(messages.c.read_at.is_(None))
        .group_by(messages.c.scope_id)
    )
    if platform is not None:
        query = query.where(messages.c.platform == platform)
    return {scope: int(count) for scope, count in conn.execute(query).all()}


def unread_counts_by_session(
    conn: Connection,
    *,
    platform: Optional[str] = None,
) -> dict[str, int]:
    """Return ``{session_id: count}`` for unread agent ``result`` messages.

    Per-session granularity for the sidebar: a project can hold several
    sessions, so a scope-level count (see ``unread_counts``) would stamp the
    same badge on every session row. Rows with a null ``session_id`` are
    skipped — they can't be attributed to a specific session. Filtered to
    ``type='result'`` so the sidebar badge matches the inbox card's unread
    count (the realtime ``inbox.session.updated`` row is result-only too).
    """

    query = (
        select(messages.c.session_id, func.count(messages.c.id))
        .where(messages.c.author == "agent")
        .where(messages.c.type == "result")
        .where(messages.c.read_at.is_(None))
        .where(messages.c.session_id.is_not(None))
        .group_by(messages.c.session_id)
    )
    if platform is not None:
        query = query.where(messages.c.platform == platform)
    return {session_id: int(count) for session_id, count in conn.execute(query).all()}


def list_inbox_sessions(
    conn: Connection,
    *,
    platform: Optional[str] = "avibe",
    unread_only: bool = False,
    limit: int = 30,
    before: Optional[str] = None,
    only_session: Optional[str] = None,
) -> dict[str, Any]:
    """Per-session ("Slack-like") inbox feed.

    One row per session that has at least one agent reply. Sorted by the
    session's most recent message of *any* author (the activity clock),
    descending. The preview text is the session's latest *agent* reply
    (distinct from the sort key). ``replied`` is True when the most recent
    message is the user's (they've responded, awaiting the agent).

    Keyset pagination via ``before`` (an opaque ``"<last_activity_at>|<session_id>"``
    cursor returned as ``next_cursor``).
    """

    m = messages

    # Rank every message in a session by recency (any author) → latest = activity clock.
    # Exclude the ephemeral queue/draft rows: they live in this table (Step 5)
    # but aren't sent conversation, so a saved draft or a pending queued message
    # must NOT bump the session to the top of the inbox or flip its "replied"
    # badge (Codex P2).
    any_ranked = (
        select(
            m.c.session_id.label("session_id"),
            m.c.scope_id.label("scope_id"),
            m.c.author.label("last_author"),
            m.c.created_at.label("last_activity_at"),
            func.row_number()
            .over(partition_by=m.c.session_id, order_by=(m.c.created_at.desc(), m.c.id.desc()))
            .label("rn"),
        )
        .where(m.c.session_id.is_not(None))
        .where(m.c.type.notin_(NON_CONVERSATION_TYPES))
    )
    if platform is not None:
        any_ranked = any_ranked.where(m.c.platform == platform)
    any_ranked = any_ranked.subquery()
    latest_any = select(any_ranked).where(any_ranked.c.rn == 1).subquery()

    # Rank agent messages by recency → latest agent reply = preview (also proves
    # eligibility). Include ``notify`` as well as ``result``: a turn that FAILS
    # before producing any result persists only a terminal ``notify``, and that
    # failed conversation must still surface in the inbox (with its error) rather
    # than disappear once the user leaves the Chat page (Codex P2). Unread counts
    # below stay result-only — a failure notify isn't an unread reply.
    agent_ranked = (
        select(
            m.c.session_id.label("session_id"),
            m.c.content_text.label("preview_text"),
            m.c.content_json.label("preview_json"),
            m.c.created_at.label("preview_at"),
            func.row_number()
            .over(partition_by=m.c.session_id, order_by=(m.c.created_at.desc(), m.c.id.desc()))
            .label("rn"),
        )
        .where(m.c.session_id.is_not(None))
        .where(m.c.type.in_(("result", "notify")))
    )
    if platform is not None:
        agent_ranked = agent_ranked.where(m.c.platform == platform)
    agent_ranked = agent_ranked.subquery()
    latest_agent = select(agent_ranked).where(agent_ranked.c.rn == 1).subquery()

    # Unread agent messages per session.
    unread_q = (
        select(m.c.session_id.label("session_id"), func.count().label("unread_count"))
        .where(m.c.session_id.is_not(None))
        .where(m.c.type == "result")
        .where(m.c.read_at.is_(None))
        .group_by(m.c.session_id)
    )
    if platform is not None:
        unread_q = unread_q.where(m.c.platform == platform)
    unread_sub = unread_q.subquery()

    unread_count_col = func.coalesce(unread_sub.c.unread_count, 0)
    query = (
        select(
            latest_agent.c.session_id,
            latest_agent.c.preview_text,
            latest_agent.c.preview_json,
            latest_agent.c.preview_at,
            latest_any.c.last_author,
            latest_any.c.last_activity_at,
            agent_sessions.c.title,
            agent_sessions.c.scope_id,
            scopes.c.native_id.label("project_id"),
            scopes.c.display_name.label("project_name"),
            unread_count_col.label("unread_count"),
        )
        .select_from(
            latest_agent.join(latest_any, latest_any.c.session_id == latest_agent.c.session_id)
            .join(agent_sessions, agent_sessions.c.id == latest_agent.c.session_id)
            .join(scopes, scopes.c.id == agent_sessions.c.scope_id, isouter=True)
            .join(unread_sub, unread_sub.c.session_id == latest_agent.c.session_id, isouter=True)
        )
    )
    if unread_only:
        query = query.where(unread_count_col > 0)
    if only_session:
        query = query.where(latest_agent.c.session_id == only_session)
    if before:
        cursor_at, _, cursor_session = before.partition("|")
        if cursor_at and cursor_session:
            query = query.where(
                or_(
                    latest_any.c.last_activity_at < cursor_at,
                    and_(
                        latest_any.c.last_activity_at == cursor_at,
                        latest_agent.c.session_id < cursor_session,
                    ),
                )
            )

    effective_limit = min(max(int(limit), 1), 100)
    query = query.order_by(
        latest_any.c.last_activity_at.desc(), latest_agent.c.session_id.desc()
    ).limit(effective_limit)

    rows = conn.execute(query).mappings().all()
    sessions: list[dict[str, Any]] = []
    for row in rows:
        preview = row["preview_text"]
        if not preview and row["preview_json"]:
            try:
                preview = (json.loads(row["preview_json"]) or {}).get("text") or ""
            except json.JSONDecodeError:
                preview = ""
        unread = int(row["unread_count"] or 0)
        sessions.append(
            {
                "session_id": row["session_id"],
                "scope_id": row["scope_id"],
                "project_id": row["project_id"],
                "project_name": row["project_name"],
                "title": row["title"],
                "last_activity_at": row["last_activity_at"],
                "last_message_author": row["last_author"],
                "replied": row["last_author"] == "user",
                "preview_text": preview or "",
                "preview_at": row["preview_at"],
                "unread_count": unread,
                "unread": unread > 0,
            }
        )

    next_cursor = None
    if len(sessions) == effective_limit:
        tail = sessions[-1]
        next_cursor = f"{tail['last_activity_at']}|{tail['session_id']}"
    return {"sessions": sessions, "next_cursor": next_cursor}


def get_inbox_session(
    conn: Connection,
    session_id: str,
    *,
    platform: Optional[str] = "avibe",
) -> Optional[dict[str, Any]]:
    """Return one session's inbox row (or None if it has no agent ``result`` /
    terminal ``notify`` yet). Used to build realtime ``inbox.session.updated``
    payloads."""
    rows = list_inbox_sessions(conn, platform=platform, only_session=session_id, limit=1)["sessions"]
    return rows[0] if rows else None


def mark_session_read(
    conn: Connection,
    session_id: str,
    *,
    until_message_id: Optional[str] = None,
) -> int:
    """Mark unread agent messages in a session as read, up to ``until_message_id``.

    Returns the number of rows updated.
    """

    now = _utc_now_iso()
    base = (
        update(messages)
        .where(messages.c.session_id == session_id)
        .where(messages.c.author == "agent")
        .where(messages.c.read_at.is_(None))
        .values(read_at=now, updated_at=now)
    )
    if until_message_id:
        anchor = conn.execute(
            select(messages.c.created_at).where(messages.c.id == until_message_id)
        ).scalar_one_or_none()
        if anchor is not None:
            # ``created_at`` is stored at second precision, so a bare
            # ``<= anchor`` would also mark newer messages created in the
            # same second as read. Tie-break on ``id`` so only rows at-or-
            # before the anchor message itself are affected.
            base = base.where(
                or_(
                    messages.c.created_at < anchor,
                    and_(
                        messages.c.created_at == anchor,
                        messages.c.id <= until_message_id,
                    ),
                )
            )
    result = conn.execute(base)
    return result.rowcount or 0


def list_messages_for_inbox_scope(
    conn: Connection,
    scope_id: str,
    *,
    limit: int = 1,
) -> Iterable[dict[str, Any]]:
    """Return the latest N messages for a given scope (for inbox previews)."""

    query = (
        select(messages)
        .where(messages.c.scope_id == scope_id)
        .order_by(messages.c.created_at.desc(), messages.c.id.desc())
        .limit(min(max(int(limit), 1), 50))
    )
    return [_row_to_payload(dict(row)) for row in conn.execute(query).mappings().all()]
