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
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import Connection

from storage.models import agent_sessions, messages, scopes


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:16]}"


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
    message_type: str = "assistant",
    text: Optional[str] = None,
    content: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    author_id: Optional[str] = None,
    author_name: Optional[str] = None,
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

    now = _utc_now_iso()
    payload = {
        "id": _new_message_id(),
        "scope_id": scope_id,
        "session_id": session_id,
        "platform": platform,
        "author": author,
        "type": message_type,
        "author_id": author_id,
        "author_name": author_name,
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
) -> dict[str, Any]:
    """Return messages for one session in chronological order with cursor pagination."""

    query = select(messages).where(messages.c.session_id == session_id)
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
    effective_limit = min(max(int(limit), 1), 500)
    query = query.order_by(messages.c.created_at.asc(), messages.c.id.asc()).limit(effective_limit)
    rows = [_row_to_payload(dict(row)) for row in conn.execute(query).mappings().all()]
    # Compare against the clamped page size; a caller requesting > 500
    # would otherwise receive a full 500-row page with a null cursor and
    # silently stop paginating.
    next_after = rows[-1]["id"] if len(rows) == effective_limit else None
    return {"messages": rows, "next_after_id": next_after}


def list_inbox(
    conn: Connection,
    *,
    platform: Optional[str] = None,
    unread_only: bool = False,
    limit: int = 30,
    before_id: Optional[str] = None,
) -> dict[str, Any]:
    """Cross-session feed for the workbench Inbox.

    Returns the most recent agent-authored messages first (the human
    sent them, they don't need to see their own as "new"). The
    ``platform`` filter defaults to nothing so users opting into
    cross-platform mirroring see Slack/Discord here too; per the
    workbench scope the UI passes ``platform='avibe'`` to scope it
    down.
    """

    query = select(messages).where(messages.c.author == "agent")
    if platform is not None:
        query = query.where(messages.c.platform == platform)
    if unread_only:
        query = query.where(messages.c.read_at.is_(None))
    if before_id:
        anchor = conn.execute(
            select(messages.c.created_at).where(messages.c.id == before_id)
        ).scalar_one_or_none()
        if anchor is not None:
            query = query.where(
                or_(
                    messages.c.created_at < anchor,
                    and_(messages.c.created_at == anchor, messages.c.id < before_id),
                )
            )
    effective_limit = min(max(int(limit), 1), 200)
    query = query.order_by(messages.c.created_at.desc(), messages.c.id.desc()).limit(effective_limit)
    rows = [_row_to_payload(dict(row)) for row in conn.execute(query).mappings().all()]
    # Same fix as ``list_session_messages``: gate the cursor on the
    # clamped page size, not the raw caller-supplied limit.
    next_before = rows[-1]["id"] if len(rows) == effective_limit else None
    return {"messages": rows, "next_before_id": next_before}


def unread_counts(
    conn: Connection,
    *,
    platform: Optional[str] = None,
) -> dict[str, int]:
    """Return ``{scope_id: count}`` for unread agent messages.

    Used by the sidebar / hover popover to show per-session unread dots
    plus the global count without dragging every row through Python.
    """

    query = (
        select(messages.c.scope_id, func.count(messages.c.id))
        .where(messages.c.author == "agent")
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
    """Return ``{session_id: count}`` for unread agent messages.

    Per-session granularity for the sidebar: a project can hold several
    sessions, so a scope-level count (see ``unread_counts``) would stamp the
    same badge on every session row. Rows with a null ``session_id`` are
    skipped — they can't be attributed to a specific session.
    """

    query = (
        select(messages.c.session_id, func.count(messages.c.id))
        .where(messages.c.author == "agent")
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
    )
    if platform is not None:
        any_ranked = any_ranked.where(m.c.platform == platform)
    any_ranked = any_ranked.subquery()
    latest_any = select(any_ranked).where(any_ranked.c.rn == 1).subquery()

    # Rank agent messages by recency → latest agent reply = preview (also proves eligibility).
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
        .where(m.c.type == "result")
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
    """Return one session's inbox row (or None if it has no agent ``result``
    yet). Used to build realtime ``inbox.session.updated`` payloads."""
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
