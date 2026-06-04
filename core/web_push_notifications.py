"""Notification dispatch from durable Workbench inbox events to Web Push."""

from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy import select

from storage import web_push_service
from storage.models import agent_sessions

logger = logging.getLogger(__name__)

_NOTIFIABLE_TYPES = {"result", "notify", "error"}
_LOCAL_USER_KEY = "local"


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


def _user_key_for_session(conn: Any, session_id: str | None) -> str:
    if not session_id:
        return _LOCAL_USER_KEY
    row = conn.execute(
        select(agent_sessions.c.metadata_json).where(agent_sessions.c.id == session_id)
    ).first()
    if row is None:
        return _LOCAL_USER_KEY
    try:
        import json

        metadata = json.loads(row[0] or "{}")
    except Exception:
        return _LOCAL_USER_KEY
    user_key = metadata.get("_web_push_user_key") if isinstance(metadata, dict) else None
    return user_key if isinstance(user_key, str) and user_key.strip() else _LOCAL_USER_KEY


def _send_to_enabled_subscriptions(payload: dict[str, Any]) -> None:
    from core.web_push import send_web_push
    from storage.db import create_sqlite_engine

    engine = create_sqlite_engine()
    try:
        with engine.begin() as conn:
            user_key = _user_key_for_session(conn, payload.get("session_id"))
            subscriptions = web_push_service.list_enabled(conn, user_key=user_key)
            for subscription in subscriptions:
                try:
                    send_web_push(subscription=subscription, payload=payload)
                    web_push_service.mark_send_success(conn, endpoint=subscription["endpoint"])
                except Exception as exc:
                    status_code = getattr(getattr(exc, "response", None), "status_code", None)
                    disable = status_code in {404, 410}
                    logger.warning("web push: send failed", exc_info=True)
                    web_push_service.mark_send_failure(
                        conn,
                        endpoint=subscription["endpoint"],
                        disable=disable,
                    )
    finally:
        engine.dispose()
