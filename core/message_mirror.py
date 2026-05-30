"""Best-effort mirror of inbound + outbound IM traffic into ``messages``.

This is the cross-platform write path that feeds the workbench: every
non-avibe IM event (Slack DM, Discord channel reply, Telegram private
chat, Lark/Feishu group ping, WeChat push) lands a row in the same
``messages`` table that avibe sessions write to. Downstream views — the
Inbox feed, per-session transcript, future cross-platform search — read
from one shape regardless of origin.

Hooks live in two places:

* ``core/handlers/message_handler.py`` calls :func:`mirror_inbound` once
  per human-originated turn, after session resolution.
* ``core/message_dispatcher.py`` calls :func:`persist_agent_message` once per
  agent ``emit_agent_message`` — for every type (result / assistant /
  tool_call / notify), on every platform incl. avibe, BEFORE the IM mute
  filter so hidden messages still land.

Failures are swallowed and logged. A bad mirror write must never break
the live IM reply path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError

from modules.im.base import MessageContext
from storage import messages_service, settings_service
from storage.db import create_sqlite_engine

logger = logging.getLogger(__name__)

# Non-avibe IM scopes are stored as 'channel' rows — one per DM/group/topic.
# avibe projects are 'project' typed and pre-created via ``/api/projects``;
# this module never touches those.
DEFAULT_SCOPE_TYPE = "channel"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_scope_id(conn, context: MessageContext) -> Optional[str]:
    platform = (context.platform or "").strip()
    native_id = (context.channel_id or "").strip()
    if not platform or not native_id:
        return None
    try:
        return settings_service.upsert_scope(
            conn,
            platform=platform,
            scope_type=DEFAULT_SCOPE_TYPE,
            native_id=native_id,
            now=_now(),
            supports_threads=bool(context.thread_id),
        )
    except Exception:
        logger.exception("mirror: failed to upsert scope for %s::%s", platform, native_id)
        return None


def _append_quietly(conn, **kwargs) -> None:
    """Insert one row, swallowing the unique-constraint clash that fires
    when the same native message id is delivered twice (rare retry path).
    """
    try:
        messages_service.append(conn, **kwargs)
    except IntegrityError:
        logger.debug(
            "mirror: skipped duplicate native_message_id %s on platform %s",
            kwargs.get("native_message_id"),
            kwargs.get("platform"),
        )


def _scope_id_for_session(conn, session_id: str) -> Optional[str]:
    """Resolve a message's scope from its agent session (works for avibe +
    IM once the session has been reserved)."""
    from sqlalchemy import select

    from storage.models import agent_sessions

    row = conn.execute(
        select(agent_sessions.c.scope_id).where(agent_sessions.c.id == session_id)
    ).first()
    return row[0] if row else None


# Maps the dispatcher's canonical message type to the persisted ``messages.type``.
# ``system`` folds into ``notify`` (a process message, not a user-facing reply)
# so it never pollutes the result-only inbox preview.
_AGENT_TYPE_BY_CANONICAL = {
    "result": "result",
    "notify": "notify",
    "assistant": "assistant",
    "toolcall": "tool_call",
    "tool_call": "tool_call",
    "system": "notify",
}


def persist_agent_message(context: MessageContext, canonical_type: str, text: str) -> None:
    """Persist one agent output into the workbench ``messages`` store.

    Unified across **all** platforms (including avibe, which has no IM mirror)
    and called BEFORE any IM delivery/mute decision, so assistant / tool_call
    messages land even when a channel hides them. Each ``emit_agent_message``
    call is a distinct logical message — the consolidated IM "log" message only
    merges them for display — so one row per emit is correct, not fragments.

    ``context`` is the **post-routing delivery target** (see
    ``emit_agent_message``): IM rows are attributed to the channel that actually
    received the reply, so routed / ``post_to`` / thread replies are recorded
    under their delivery scope rather than the source session's — keeping
    cross-platform history/search pointed at the right conversation. avibe rows
    instead use the session's project scope (``agent_session_id`` from
    ``context.platform_specific``), which is what the per-session inbox groups on.
    """
    if not text or not text.strip():
        return
    if not context.platform:
        return
    message_type = _AGENT_TYPE_BY_CANONICAL.get(canonical_type or "", "assistant")
    session_id = (context.platform_specific or {}).get("agent_session_id")
    try:
        engine = create_sqlite_engine()
        inbox_row = None
        with engine.begin() as conn:
            if context.platform == "avibe":
                # Inbox groups by the avibe session's project scope; never invent
                # a 'channel' scope for avibe (projects are pre-created via
                # /api/projects). Skip if the session row isn't visible yet.
                scope_id = _scope_id_for_session(conn, session_id) if session_id else None
                row_session_id = session_id
            else:
                # IM: attribute the row to the delivery channel (this ``context``
                # is the routed target), matching where the reply was sent. The
                # cross-platform history is scope-keyed, not session-keyed, so the
                # row carries no session_id — same shape as ``mirror_inbound``.
                scope_id = _resolve_scope_id(conn, context)
                row_session_id = None
            if scope_id is None:
                return
            _append_quietly(
                conn,
                scope_id=scope_id,
                session_id=row_session_id,
                platform=context.platform,
                author="agent",
                message_type=message_type,
                text=text,
                parent_native_message_id=context.thread_id,
                content={"kind": canonical_type} if canonical_type else None,
            )
            # Recompute the session's inbox row so the realtime event can patch
            # the browser without a refetch. avibe-only: the workbench inbox is
            # scoped to avibe sessions (IM rows persist but aren't shown there).
            if context.platform == "avibe" and session_id:
                inbox_row = messages_service.get_inbox_session(conn, session_id)
        if inbox_row is not None:
            from core.inbox_events import bus

            bus.publish("inbox.session.updated", inbox_row)
    except Exception:
        logger.exception("persist_agent_message: failure on platform=%s", context.platform)


def mirror_inbound(context: MessageContext, text: str) -> None:
    """Record a human-originated message into the messages table."""

    if not text or not text.strip():
        return
    if not context.platform:
        return
    if context.platform == "avibe":
        # avibe's REST endpoint already writes through ``messages_service``;
        # mirroring here would double-count the row.
        return
    try:
        engine = create_sqlite_engine()
        with engine.begin() as conn:
            scope_id = _resolve_scope_id(conn, context)
            if scope_id is None:
                return
            _append_quietly(
                conn,
                scope_id=scope_id,
                session_id=None,
                platform=context.platform,
                author="user",
                message_type="user",
                text=text,
                author_id=context.user_id,
                native_message_id=context.message_id,
                parent_native_message_id=context.thread_id,
            )
    except Exception:
        logger.exception("mirror_inbound: unexpected failure on platform=%s", context.platform)
