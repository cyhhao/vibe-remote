"""Append-only trace events emitted by agent runtimes.

``agent_events`` is intentionally separate from ``messages``: rows here are
debug/trace material, not transcript content. The first writer is tool-call
output from backend SDK streams, which should be retained for diagnosis without
ever becoming a chat/inbox message.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.engine import Connection

from storage.models import agent_events


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_event_id() -> str:
    return f"evt_{int(time.time() * 1_000_000):015x}{uuid.uuid4().hex[:8]}"


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
        "turn_id": row.get("turn_id"),
        "run_id": row.get("run_id"),
        "platform": row.get("platform"),
        "agent_name": row.get("agent_name"),
        "backend": row.get("backend"),
        "event_type": row.get("event_type"),
        "visibility": row.get("visibility"),
        "sequence": row.get("sequence"),
        "text": row.get("content_text") or content.get("text") or "",
        "content": content,
        "metadata": metadata,
        "source": row.get("source"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def append(
    conn: Connection,
    *,
    scope_id: str,
    session_id: Optional[str],
    platform: str,
    event_type: str,
    text: Optional[str] = None,
    content: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    agent_name: Optional[str] = None,
    backend: Optional[str] = None,
    turn_id: Optional[str] = None,
    run_id: Optional[str] = None,
    visibility: str = "trace",
    source: Optional[str] = "agent",
    sequence: Optional[int] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if content:
        body.update(content)
    if text is not None:
        body.setdefault("text", text)
    plain = text if text is not None else body.get("text") or None

    now = _utc_now_iso()
    payload = {
        "id": _new_event_id(),
        "scope_id": scope_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "run_id": run_id,
        "platform": platform,
        "agent_name": agent_name,
        "backend": backend,
        "event_type": event_type,
        "visibility": visibility,
        "sequence": sequence,
        "content_text": plain,
        "content_json": json.dumps(body),
        "metadata_json": json.dumps(metadata or {}),
        "source": source,
        "created_at": now,
        "updated_at": now,
    }
    conn.execute(agent_events.insert().values(**payload))
    return _row_to_payload(payload)
