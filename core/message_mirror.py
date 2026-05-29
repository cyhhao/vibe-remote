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
* ``core/message_dispatcher.py`` calls :func:`mirror_outbound` once per
  successful agent ``result`` / ``notify`` send.

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
                text=text,
                author_id=context.user_id,
                native_message_id=context.message_id,
                parent_native_message_id=context.thread_id,
            )
    except Exception:
        logger.exception("mirror_inbound: unexpected failure on platform=%s", context.platform)


def mirror_outbound(
    context: MessageContext,
    text: str,
    *,
    native_message_id: Optional[str],
    kind: str = "result",
) -> None:
    """Record a successful agent reply into the messages table."""

    if not text or not text.strip():
        return
    if not context.platform:
        return
    if context.platform == "avibe":
        # Avibe (workbench Web UI) replies are session-scoped, not
        # channel-scoped: the user message was already written under the
        # workbench session by ``vibe/ui_server.py``, so the agent reply
        # must land in that same session + project scope. Route it through
        # the session-aware writer instead of the channel ``upsert_scope``
        # path used for external IM platforms.
        _mirror_avibe_outbound(context, text, native_message_id=native_message_id, kind=kind)
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
                author="agent",
                text=text,
                native_message_id=native_message_id,
                parent_native_message_id=context.thread_id,
                content={"kind": kind} if kind else None,
            )
    except Exception:
        logger.exception("mirror_outbound: unexpected failure on platform=%s", context.platform)


def _mirror_avibe_outbound(
    context: MessageContext,
    text: str,
    *,
    native_message_id: Optional[str],
    kind: str,
) -> None:
    """Persist a workbench (avibe) agent reply into the session's transcript.

    The session id rides on ``context.platform_specific["workbench_session_id"]``
    (set by ``core/internal_server.py`` when it builds the dispatch context);
    it also falls back to ``channel_id``, which the dispatch payload defaults
    to the session id. The session row carries the project ``scope_id`` the
    user message was stored under, so the reply joins the same transcript and
    survives the post-stream ``refresh()`` in the Chat page.
    """

    spec = context.platform_specific or {}
    session_id = spec.get("workbench_session_id") or context.channel_id
    if not session_id:
        return
    try:
        from core.services import sessions as sessions_service

        engine = create_sqlite_engine()
        with engine.begin() as conn:
            try:
                session = sessions_service.get_session(conn, session_id)
            except LookupError:
                return
            _append_quietly(
                conn,
                scope_id=session.get("scope_id"),
                session_id=session_id,
                platform="avibe",
                author="agent",
                text=text,
                native_message_id=native_message_id,
                content={"kind": kind} if kind else None,
            )
    except Exception:
        logger.exception("mirror_outbound(avibe): failed to persist reply for session=%s", session_id)
