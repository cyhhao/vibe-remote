"""Notification dispatch from durable Workbench inbox events to Web Push."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlalchemy import select

from storage import web_push_service
from storage.models import messages

logger = logging.getLogger(__name__)

_NOTIFIABLE_TYPES = {"result", "notify", "error"}
WEB_PUSH_NOTIFICATION_DELAY_SECONDS = 3.0


def maybe_notify_inbox_message(message: dict[str, Any] | None, inbox_row: dict[str, Any] | None) -> None:
    """Schedule Web Push for a newly persisted inbox-visible Workbench message.

    Called after the message row and inbox row exist in the same durable write
    path. Sending happens on a background thread with its own SQLite connection
    so a slow push service never blocks message persistence or SSE fan-out.
    """

    if not message or not inbox_row:
        return
    if message.get("platform") != "avibe":
        return
    if message.get("author") != "agent":
        return
    if message.get("type") not in _NOTIFIABLE_TYPES:
        return
    if not message.get("session_id"):
        return

    payload = {
        "title": inbox_row.get("title") or inbox_row.get("project_name") or "Vibe Remote",
        "body": (message.get("text") or inbox_row.get("preview_text") or "").strip()[:240],
        "url": f"/chat/{message['session_id']}",
        "tag": f"session:{message['session_id']}",
        "badge_count": inbox_row.get("unread_count") or 0,
        "message_id": message.get("id"),
        "session_id": message.get("session_id"),
    }
    thread = threading.Thread(target=_send_to_enabled_subscriptions, args=(payload,), daemon=True)
    thread.start()


def _message_still_unread(conn: Any, message_id: str | None) -> bool:
    if not message_id:
        return False
    row = conn.execute(
        select(messages.c.read_at)
        .where(messages.c.id == message_id)
        .where(messages.c.platform == "avibe")
        .where(messages.c.author == "agent")
        .where(messages.c.type.in_(_NOTIFIABLE_TYPES))
    ).first()
    return bool(row is not None and row[0] is None)


def _web_push_user_key_for_message(conn: Any, message_id: str | None) -> str | None:
    """Resolve the browser owner for a Workbench agent message.

    New sessions should not carry a Web Push owner field: that made pre-existing
    sessions invisible to push. Instead, recover ownership from the most recent
    Web UI user message in the same session. Local installs share one namespace;
    remote-access browser sessions write a ``remote:<sub>`` author_id.
    """

    if not message_id:
        return None
    agent_row = conn.execute(
        select(messages.c.session_id, messages.c.created_at, messages.c.id)
        .where(messages.c.id == message_id)
        .where(messages.c.platform == "avibe")
        .where(messages.c.author == "agent")
    ).first()
    if not agent_row or not agent_row[0]:
        return None
    session_id, created_at, row_id = agent_row
    user_row = conn.execute(
        select(messages.c.author_id)
        .where(messages.c.session_id == session_id)
        .where(messages.c.platform == "avibe")
        .where(messages.c.author == "user")
        .where(messages.c.author_id.is_not(None))
        .where(
            (messages.c.created_at < created_at)
            | ((messages.c.created_at == created_at) & (messages.c.id < row_id))
        )
        .order_by(messages.c.created_at.desc(), messages.c.id.desc())
        .limit(1)
    ).first()
    if not user_row or not isinstance(user_row[0], str) or not user_row[0].strip():
        return None
    return user_row[0].strip()


def _send_to_enabled_subscriptions(payload: dict[str, Any]) -> None:
    from core.web_push import send_web_push
    from storage.db import create_sqlite_engine

    delay = max(0.0, WEB_PUSH_NOTIFICATION_DELAY_SECONDS)
    if delay:
        time.sleep(delay)

    engine = create_sqlite_engine()
    try:
        with engine.connect() as conn:
            if not _message_still_unread(conn, payload.get("message_id")):
                logger.debug("web push: skip notification for message already read or missing")
                return
            user_key = _web_push_user_key_for_message(conn, payload.get("message_id"))
            if user_key is None:
                user_key = web_push_service.get_single_enabled_user_key(conn)
            if user_key is None:
                logger.debug("web push: skip notification without a unique subscription owner")
                return
            subscriptions = web_push_service.list_enabled(conn, user_key=user_key)
        for subscription in subscriptions:
            try:
                send_web_push(subscription=subscription, payload=payload)
                with engine.begin() as conn:
                    web_push_service.mark_send_success(conn, endpoint=subscription["endpoint"])
            except Exception as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                disable = status_code in {404, 410}
                logger.warning("web push: send failed", exc_info=True)
                with engine.begin() as conn:
                    web_push_service.mark_send_failure(
                        conn,
                        endpoint=subscription["endpoint"],
                        disable=disable,
                    )
    finally:
        engine.dispose()
