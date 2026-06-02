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


def _append_quietly(conn, **kwargs) -> Optional[dict]:
    """Insert one row and return its payload, swallowing the unique-constraint
    clash that fires when the same native message id is delivered twice (rare
    retry path). Returns ``None`` on the swallowed duplicate so callers can skip
    the realtime ``message.new`` publish for a row that didn't materialize.
    """
    try:
        return messages_service.append(conn, **kwargs)
    except IntegrityError:
        logger.debug(
            "mirror: skipped duplicate native_message_id %s on platform %s",
            kwargs.get("native_message_id"),
            kwargs.get("platform"),
        )
        return None


def _publish_session_message(row: Optional[dict]) -> None:
    """Publish a session-scoped ``message.new`` for a freshly persisted row.

    The Controller process persists agent + harness rows; this fans the row out
    over ``inbox_events.bus`` → ``/internal/events`` → ``inbox_bridge`` →
    browser ``SSEBroker`` (the #359 path), so an open Chat page appends it live —
    the session/page-scoped stream that replaces per-turn SSE. Scoped to rows
    that carry a ``session_id`` (avibe sessions); IM rows are scope-keyed and the
    workbench Chat is avibe-only, so they have no live consumer.
    """
    if not row or not row.get("session_id"):
        return
    try:
        from core.inbox_events import bus

        bus.publish("message.new", row)
    except Exception:
        logger.debug("message_mirror: message.new publish failed", exc_info=True)


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
# ``system`` folds into ``assistant`` (a process-log message, not a user-facing
# reply): once terminal-failure ``notify`` rows became inbox-eligible, routine
# system/init logs stored as ``notify`` would have created an Inbox card with a
# junk preview before any real reply. As process log, ``system`` belongs with
# ``assistant`` / ``tool_call`` — out of the inbox and out of the transcript
# (Codex P2). Genuine terminal failures persist via canonical ``notify``.
_AGENT_TYPE_BY_CANONICAL = {
    "result": "result",
    "notify": "notify",
    "assistant": "assistant",
    "toolcall": "tool_call",
    "tool_call": "tool_call",
    "system": "assistant",
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
        appended_row = None
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
            # Provenance: every agent reply is source='agent'; name = the
            # session's agent (from the dispatch context). source_id (author_id)
            # is left to the agent-id wiring later; the session already carries it.
            spec = context.platform_specific or {}
            agent_name = spec.get("vibe_agent_name") or (spec.get("agent_session_target") or {}).get("agent_name")
            # Workbench Chat only: rewrite ``file://`` links in the persisted copy
            # to same-origin media-proxy URLs so the browser renders agent images
            # inline + files as download cards. IM rows keep the raw ``file://``
            # (the dispatcher uploads those to the platform separately). Scoped to
            # the user-visible result/notify rows so we don't mint tokens for the
            # hidden intermediate assistant/tool_call stream.
            if context.platform == "avibe" and message_type in ("result", "notify") and row_session_id:
                try:
                    from core.workbench_media import rewrite_agent_media

                    text = rewrite_agent_media(conn, scope_id=scope_id, session_id=row_session_id, text=text)
                except Exception:
                    logger.exception("persist_agent_message: media rewrite failed")
            appended_row = _append_quietly(
                conn,
                scope_id=scope_id,
                session_id=row_session_id,
                platform=context.platform,
                author="agent",
                source="agent",
                author_name=agent_name,
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
        # Fan the row out to an open Chat page (session-scoped stream), then bump
        # the inbox card. Both ride the controller→browser bridge.
        _publish_session_message(appended_row)
        if inbox_row is not None:
            from core.inbox_events import bus

            bus.publish("inbox.session.updated", inbox_row)
    except Exception:
        logger.exception("persist_agent_message: failure on platform=%s", context.platform)


def mirror_harness_inbound(context: MessageContext, text: str) -> None:
    """Record a harness-originated prompt (scheduled task / watch / webhook).

    Harness turns inject a *user-role* prompt into a session that the human
    never typed, so the row is ``author='user'`` (the agent reads it as user
    input) but ``source='harness'`` — the transcript can then mark it as
    triggered by a scheduled task / watch instead of the user. ``author_name``
    carries the trigger kind (scheduled / watch / webhook / ...) and
    ``author_id`` the run-definition id, per the provenance spec.

    Unlike :func:`mirror_inbound` this *does* cover avibe: no REST endpoint
    writes the harness prompt, so without this the workbench transcript would
    show an agent reply with no originating turn. Scope resolution mirrors
    :func:`persist_agent_message` — avibe rows attach to the session's project
    scope, IM rows to the delivery channel.
    """
    if not text or not text.strip():
        return
    if not context.platform:
        return
    spec = context.platform_specific or {}
    trigger_kind = spec.get("task_trigger_kind")
    definition_id = spec.get("task_definition_id")
    session_id = spec.get("agent_session_id")
    try:
        engine = create_sqlite_engine()
        appended_row = None
        inbox_row = None
        with engine.begin() as conn:
            if context.platform == "avibe":
                scope_id = _scope_id_for_session(conn, session_id) if session_id else None
                row_session_id = session_id
            else:
                # Attribute the prompt to the SAME scope the reply lands in. A
                # scheduled/watch run with a delivery override (post_to / a
                # different deliver-key) sends its result to the override channel
                # (see ``emit_agent_message``); resolve the prompt there too so
                # one turn isn't split across the source + delivery scopes (Codex
                # P2). Falls back to the source context when there's no override.
                deliver_ctx = context
                override = spec.get("delivery_override") or {}
                if override.get("channel_id"):
                    deliver_ctx = MessageContext(
                        user_id=override.get("user_id") or context.user_id,
                        channel_id=override["channel_id"],
                        platform=override.get("platform") or context.platform,
                        thread_id=override.get("thread_id"),
                    )
                scope_id = _resolve_scope_id(conn, deliver_ctx)
                row_session_id = None
            if scope_id is None:
                return
            appended_row = _append_quietly(
                conn,
                scope_id=scope_id,
                session_id=row_session_id,
                platform=context.platform,
                author="user",
                source="harness",
                author_name=trigger_kind,
                author_id=definition_id,
                message_type="user",
                text=text,
                native_message_id=context.message_id,
                parent_native_message_id=context.thread_id,
            )
            # Recompute the inbox card so the harness prompt re-ranks the session
            # + flips its activity for other open views (avibe only; the inbox is
            # avibe-scoped). No-op until the session has a result row.
            if context.platform == "avibe" and row_session_id:
                inbox_row = messages_service.get_inbox_session(conn, row_session_id)
        # Surface the harness-triggered prompt on an open Chat page immediately,
        # so the upcoming agent reply isn't shown with no originating turn.
        _publish_session_message(appended_row)
        if inbox_row is not None:
            from core.inbox_events import bus

            bus.publish("inbox.session.updated", inbox_row)
    except Exception:
        logger.exception("mirror_harness_inbound: unexpected failure on platform=%s", context.platform)


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
                source="user",
                message_type="user",
                text=text,
                author_id=context.user_id,
                native_message_id=context.message_id,
                parent_native_message_id=context.thread_id,
            )
    except Exception:
        logger.exception("mirror_inbound: unexpected failure on platform=%s", context.platform)
