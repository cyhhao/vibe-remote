"""Workbench-scoped session CRUD over ``agent_sessions``.

``storage/sessions_service.py`` exposes the runtime-facing primitives
that IM dispatchers use to reserve sessions during message handling.
The workbench REST API needs different shapes — listing sessions in a
project, creating one with explicit Agent / model / effort, renaming,
archiving — so this module wraps the same ``agent_sessions`` table
with workbench-friendly queries instead of bolting another concern
onto ``SQLiteSessionsService``.

Avibe scope_ids look like ``avibe::project::proj_<hex12>`` — see
``storage/projects_service.py``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection

from storage.agent_session_rows import create_agent_session_row
from storage.models import agent_sessions, scope_settings, scopes


SESSION_ID_ALPHABET = "23456789abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"

# Distinguishes an omitted update field from a present ``None`` (clear). See
# ``update_session``: a present ``model=None`` must clear the column, but an
# omitted ``model`` must leave it untouched.
_UNSET: Any = object()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    metadata = _load_metadata(row.get("metadata_json"))
    return {
        "id": row["id"],
        "scope_id": row.get("scope_id"),
        "project_id": (row.get("scope_id") or "").rsplit("::", 1)[-1] or None,
        "title": row.get("title"),
        "agent_id": row.get("agent_id"),
        "agent_name": row.get("agent_name"),
        "agent_backend": row.get("agent_backend"),
        "agent_variant": row.get("agent_variant"),
        "model": row.get("model"),
        "reasoning_effort": row.get("reasoning_effort"),
        "status": row.get("status"),
        # Live agent-runtime status (idle/running/failed), separate from the
        # lifecycle ``status``. Older rows predating the column read as ``idle``.
        "agent_status": row.get("agent_status") or "idle",
        "workdir": row.get("workdir"),
        # The reserved native-session anchor (workbench sessions self-anchor to
        # their id). Dispatch carries it so resume binds by the stored anchor
        # after a restart instead of a computed one (Codex P2).
        "session_anchor": row.get("session_anchor"),
        "native_session_id": row.get("native_session_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "last_active_at": row.get("last_active_at"),
        "metadata": metadata,
    }


def _load_metadata(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _dumps_metadata(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata)


def list_sessions(
    conn: Connection,
    *,
    scope_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    before_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return sessions for the workbench list. Cursor pagination via ``before_id``.

    ``status`` accepts ``active`` / ``archived`` (or omit for both). The
    cursor is the row id; results are sorted by ``last_active_at DESC``
    so the cursor row is "the last id you already saw".
    """

    query = select(agent_sessions)
    if scope_id is not None:
        query = query.where(agent_sessions.c.scope_id == scope_id)
    if status is not None and status != "all":
        query = query.where(agent_sessions.c.status == status)
    if before_id is not None:
        cursor_row = conn.execute(
            select(agent_sessions.c.last_active_at, agent_sessions.c.created_at).where(agent_sessions.c.id == before_id)
        ).first()
        if cursor_row is not None:
            cursor_active, cursor_created = cursor_row
            # ``last_active_at`` + ``created_at`` are both second-granularity
            # ISO strings, so multiple sessions can share the same pair and
            # become unreachable on later pages without an ``id`` tie-breaker
            # that matches the ORDER BY shape.
            query = query.where(
                (agent_sessions.c.last_active_at < cursor_active)
                | (
                    (agent_sessions.c.last_active_at == cursor_active)
                    & (agent_sessions.c.created_at < cursor_created)
                )
                | (
                    (agent_sessions.c.last_active_at == cursor_active)
                    & (agent_sessions.c.created_at == cursor_created)
                    & (agent_sessions.c.id < before_id)
                )
            )
    effective_limit = min(max(int(limit), 1), 200)
    query = (
        query.order_by(
            agent_sessions.c.last_active_at.desc(),
            agent_sessions.c.created_at.desc(),
            agent_sessions.c.id.desc(),
        )
        .limit(effective_limit)
    )
    rows = [dict(row) for row in conn.execute(query).mappings().all()]
    sessions = [_row_to_payload(row) for row in rows]
    # Use the clamped page size for the cursor check — comparing against
    # the raw ``limit`` would emit ``next_before_id=null`` for callers who
    # requested > 200 and force them to stop paginating mid-history.
    next_cursor = sessions[-1]["id"] if len(sessions) == effective_limit else None
    return {"sessions": sessions, "next_before_id": next_cursor}


def get_session(conn: Connection, session_id: str) -> dict[str, Any]:
    row = conn.execute(
        select(agent_sessions).where(agent_sessions.c.id == session_id)
    ).mappings().first()
    if row is None:
        raise LookupError(f"Session not found: {session_id}")
    return _row_to_payload(dict(row))


def create_session(
    conn: Connection,
    *,
    scope_id: str,
    agent_backend: str,
    agent_name: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_variant: Optional[str] = None,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    title: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create a workbench session inside the given project scope.

    Pulls ``workdir`` from ``scope_settings`` so Agent runs already know
    where to cd. ``native_session_id`` stays empty — Claude / OpenCode /
    Codex fill it on their first turn.
    """

    scope_row = conn.execute(
        select(
            scopes.c.id,
            scope_settings.c.workdir,
            scope_settings.c.enabled,
            scope_settings.c.agent_backend,
            scope_settings.c.agent_name,
            scope_settings.c.agent_variant,
            scope_settings.c.model,
            scope_settings.c.reasoning_effort,
        )
        .select_from(scopes.outerjoin(scope_settings, scope_settings.c.scope_id == scopes.c.id))
        .where(scopes.c.id == scope_id)
    ).mappings().first()
    if scope_row is None:
        raise LookupError(f"Scope not found: {scope_id}")
    if scope_row.get("enabled") == 0:
        raise PermissionError(f"Scope is archived: {scope_id}")
    # Inherit the project's default Agent when the caller didn't pin a backend.
    # The default lives in ``scope_settings`` (set via Project Settings); adopting
    # it at creation pins the backend from the first turn (a session's backend is
    # write-once) and makes the chat header open on the right Agent. No project
    # default → the fields stay empty and dispatch falls back to the global
    # default Vibe Agent. An explicit caller backend always wins.
    if not agent_backend and scope_row.get("agent_backend"):
        agent_backend = str(scope_row["agent_backend"])
        if agent_name is None:
            agent_name = scope_row.get("agent_name")
        if agent_variant is None:
            agent_variant = scope_row.get("agent_variant")
        if model is None:
            model = scope_row.get("model")
        if reasoning_effort is None:
            reasoning_effort = scope_row.get("reasoning_effort")

    now = _utc_now_iso()
    variant = agent_variant or agent_backend or "default"
    metadata_payload = {"created_via": "workbench"}
    if metadata:
        metadata_payload.update(metadata)

    session_id = create_agent_session_row(
        conn,
        scope_id=scope_id,
        agent_id=agent_id,
        agent_name=agent_name,
        agent_backend=agent_backend,
        agent_variant=str(variant),
        model=model,
        reasoning_effort=reasoning_effort,
        # Workbench sessions self-anchor; IM platforms use the parent message ts.
        session_anchor=None,
        workdir=scope_row.get("workdir") or os.getcwd(),
        title=title,
        metadata=metadata_payload,
        now=now,
    )
    return get_session(conn, session_id)


class SessionBackendLockedError(Exception):
    """Raised when a caller tries to switch the backend of a session that already
    has a native conversation. A session is pinned to its backend for life: the
    native can only be resumed by the backend that created it, so switching would
    strand it and silently lose context. Changing the agent WITHIN the same
    backend stays allowed."""

    def __init__(self, *, session_id: str, current_backend: Optional[str], requested_backend: Optional[str]):
        self.session_id = session_id
        self.current_backend = current_backend
        self.requested_backend = requested_backend
        super().__init__(
            f"Session {session_id} is bound to backend "
            f"'{current_backend}' and cannot switch to '{requested_backend}'."
        )


def update_session(
    conn: Connection,
    session_id: str,
    *,
    title: Any = _UNSET,
    agent_id: Any = _UNSET,
    agent_name: Any = _UNSET,
    agent_backend: Any = _UNSET,
    agent_variant: Any = _UNSET,
    model: Any = _UNSET,
    reasoning_effort: Any = _UNSET,
) -> dict[str, Any]:
    existing = conn.execute(
        select(
            agent_sessions.c.id,
            agent_sessions.c.agent_backend,
            agent_sessions.c.native_session_id,
            agent_sessions.c.metadata_json,
        ).where(agent_sessions.c.id == session_id)
    ).first()
    if existing is None:
        raise LookupError(f"Session not found: {session_id}")

    # Backend is pinned once the session has a real native conversation AND a
    # concrete backend recorded. Allow changing the agent/model/effort within the
    # SAME backend; reject a switch to a DIFFERENT backend (it would strand the
    # native and lose context). Two transitions are NOT a switch and must be
    # allowed: (a) no native yet (empty native_session_id) — backend is still free;
    # (b) a plain Workbench chat created with an EMPTY agent_backend — its first
    # real backend selection from the chat header is the initial pin, not a switch
    # away from a concrete backend (Codex P2: otherwise the chat can't pick an
    # agent/model after its first reply).
    if (
        agent_backend is not _UNSET
        and existing.native_session_id
        and str(existing.agent_backend or "")
        and str(agent_backend) != str(existing.agent_backend or "")
    ):
        raise SessionBackendLockedError(
            session_id=session_id,
            current_backend=existing.agent_backend,
            requested_backend=agent_backend,
        )

    values: dict[str, Any] = {"updated_at": _utc_now_iso()}
    if title is not _UNSET:
        cleaned = str(title or "").strip()
        values["title"] = cleaned or None
        metadata = _load_metadata(existing.metadata_json)
        metadata["title_source"] = "user"
        metadata["title_user_modified_at"] = values["updated_at"]
        values["metadata_json"] = _dumps_metadata(metadata)
    if agent_id is not _UNSET:
        values["agent_id"] = agent_id or None
    if agent_name is not _UNSET:
        values["agent_name"] = agent_name or None
    if agent_backend is not _UNSET:
        values["agent_backend"] = agent_backend or ""
    if agent_variant is not _UNSET:
        values["agent_variant"] = str(agent_variant or "default")
    # ``model`` / ``reasoning_effort`` use a sentinel default so a PRESENT
    # ``None`` clears the column (switching to an agent whose default model /
    # effort is empty must drop the previous agent's override), while an omitted
    # field leaves the stored value untouched (Codex P2).
    if model is not _UNSET:
        values["model"] = model or None
    if reasoning_effort is not _UNSET:
        values["reasoning_effort"] = reasoning_effort or None

    conn.execute(update(agent_sessions).where(agent_sessions.c.id == session_id).values(**values))
    return get_session(conn, session_id)


def backfill_session_title(
    conn: Connection,
    session_id: str,
    *,
    title: str,
    backend: str,
    source: str = "backend",
    confidence: Optional[str] = None,
    native_session_id: Optional[str] = None,
) -> dict[str, Any] | None:
    """Fill an empty Vibe session title from a backend/derived source.

    Returns the updated session payload when a title was written; returns
    ``None`` when the row is missing, already has any title, is explicitly
    user-owned, or the incoming title is empty. This is intentionally
    write-once for title content: backend sync backfills blank sessions only.
    """

    cleaned = str(title or "").strip()
    if not cleaned:
        return None

    row = conn.execute(
        select(
            agent_sessions.c.id,
            agent_sessions.c.title,
            agent_sessions.c.metadata_json,
            agent_sessions.c.native_session_id,
        ).where(agent_sessions.c.id == session_id)
    ).mappings().first()
    if row is None:
        return None

    metadata = _load_metadata(row.get("metadata_json"))
    if metadata.get("title_source") == "user":
        return None
    if str(row.get("title") or "").strip():
        return None

    now = _utc_now_iso()
    metadata.update(
        {
            "title_source": source,
            "title_backend": backend,
            "title_synced_at": now,
        }
    )
    if native_session_id:
        metadata["title_native_session_id"] = native_session_id
    elif row.get("native_session_id"):
        metadata["title_native_session_id"] = row.get("native_session_id")
    if confidence:
        metadata["title_confidence"] = confidence

    result = conn.execute(
        update(agent_sessions)
        .where(agent_sessions.c.id == session_id)
        .where((agent_sessions.c.title.is_(None)) | (agent_sessions.c.title == ""))
        .where(func.coalesce(func.json_extract(agent_sessions.c.metadata_json, "$.title_source"), "") != "user")
        .values(title=cleaned, metadata_json=_dumps_metadata(metadata), updated_at=now)
    )
    if result.rowcount == 0:
        return None
    return get_session(conn, session_id)


def archive_session(conn: Connection, session_id: str) -> dict[str, Any]:
    existing = conn.execute(
        select(agent_sessions.c.id).where(agent_sessions.c.id == session_id)
    ).scalar_one_or_none()
    if existing is None:
        raise LookupError(f"Session not found: {session_id}")
    now = _utc_now_iso()
    conn.execute(
        update(agent_sessions)
        .where(agent_sessions.c.id == session_id)
        .values(status="archived", updated_at=now)
    )
    return get_session(conn, session_id)


def touch_session(conn: Connection, session_id: str) -> None:
    """Bump ``last_active_at`` after a new message arrives."""

    conn.execute(
        update(agent_sessions)
        .where(agent_sessions.c.id == session_id)
        .values(last_active_at=_utc_now_iso(), updated_at=_utc_now_iso())
    )


VALID_AGENT_STATUSES = ("idle", "running", "failed")


def set_agent_status(conn: Connection, session_id: str, status: str) -> bool:
    """Set a session's live agent-runtime status (idle/running/failed).

    Returns ``True`` when the stored value actually changed, so the caller can
    skip a redundant ``session.status`` broadcast (and the write) when the dot
    colour wouldn't move. Unknown status / missing session is a no-op (False).
    Deliberately does NOT bump ``updated_at`` — a status flip is not a content
    edit and must not re-rank the session list.
    """

    if status not in VALID_AGENT_STATUSES:
        return False
    current = conn.execute(
        select(agent_sessions.c.agent_status).where(agent_sessions.c.id == session_id)
    ).scalar_one_or_none()
    if current is None or current == status:
        return False
    conn.execute(
        update(agent_sessions).where(agent_sessions.c.id == session_id).values(agent_status=status)
    )
    return True


def reset_running_agent_status(conn: Connection) -> int:
    """Reset every ``running`` session to ``idle`` (startup crash recovery).

    No turn survives a controller restart, so a ``running`` left in the table
    is stale. Returns the number of rows reset. The browser reconciles the reset
    by refetching sessions when its inbox-event stream (re)connects, NOT from a
    broadcast — this runs in ``Controller.__init__`` before any event subscriber
    exists, so a broadcast here would be dropped.
    """

    result = conn.execute(
        update(agent_sessions)
        .where(agent_sessions.c.agent_status == "running")
        .values(agent_status="idle")
    )
    return result.rowcount or 0
