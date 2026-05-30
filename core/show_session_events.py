from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from config import paths
from core.services import sessions as workbench_sessions_service
from storage import messages_service
from storage.db import create_sqlite_engine
from storage.importer import ensure_sqlite_state, resolve_primary_platform_from_config
from storage.models import agent_sessions, show_session_events

DEFAULT_MARK_SCOPE = "default"
SUPPORTED_EVENT_TYPES = {"assistant.mark.created", "human.intent.submitted", "human.annotation.created"}


class ShowSessionEventError(ValueError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ShowSessionEventStore:
    db_path: Path | None = None

    def __post_init__(self) -> None:
        if self.db_path is None:
            ensure_sqlite_state(primary_platform=resolve_primary_platform_from_config(paths.get_state_dir()))
        else:
            from storage.migrations import run_migrations

            run_migrations(self.db_path)
        object.__setattr__(self, "engine", create_sqlite_engine(self.db_path))

    def close(self) -> None:
        self.engine.dispose()

    def append(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        event_type = _validate_event_type(payload.get("type"))
        actor = _actor_for_event(event_type)
        event_payload = _normalize_event_payload(event_type, payload)
        anchor = _normalize_json_object(payload.get("anchor"))
        scope = _event_scope(event_type, event_payload)
        transcript_text = _format_transcript_text(event_type, event_payload, anchor)
        event_id = _event_id(payload, event_payload)
        created_at = _event_created_at(event_payload)

        with self.engine.begin() as conn:
            session = conn.execute(
                select(agent_sessions.c.id, agent_sessions.c.scope_id).where(agent_sessions.c.id == session_id).limit(1)
            ).mappings().first()
            if session is None:
                raise ShowSessionEventError("Agent session not found.", code="session_not_found")

            conn.execute(
                show_session_events.insert().values(
                    id=event_id,
                    session_id=session_id,
                    event_type=event_type,
                    actor=actor,
                    scope=scope,
                    anchor_json=_json_dumps(anchor),
                    payload_json=_json_dumps(event_payload),
                    transcript_text=transcript_text,
                    message_id=None,
                    created_at=created_at,
                )
            )
            message: dict[str, Any] | None = None
            message_id: str | None = None
            if transcript_text:
                message = messages_service.append(
                    conn,
                    scope_id=session["scope_id"],
                    session_id=session_id,
                    platform="avibe",
                    author="agent" if actor == "assistant" else "user",
                    text=transcript_text,
                    content={"text": transcript_text, "show_event_type": event_type},
                    metadata={
                        "source": "show_page",
                        "show_event_id": event_id,
                        "show_event_type": event_type,
                        "show_event_scope": scope,
                    },
                    native_message_id=f"show:{event_id}",
                )
                message_id = message["id"]
                conn.execute(
                    update(show_session_events).where(show_session_events.c.id == event_id).values(message_id=message_id)
                )
                workbench_sessions_service.touch_session(conn, session_id)

        event = {
            "id": event_id,
            "session_id": session_id,
            "scope_id": session["scope_id"],
            "type": event_type,
            "actor": actor,
            "scope": scope,
            "anchor": anchor,
            "payload": event_payload,
            "transcript_text": transcript_text,
            "message_id": message_id,
            "message": message,
            "created_at": created_at,
        }
        return event

    def list(self, session_id: str, *, after_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        effective_limit = min(max(int(limit), 1), 500)
        with self.engine.connect() as conn:
            query = select(show_session_events).where(show_session_events.c.session_id == session_id)
            if after_id:
                anchor = conn.execute(
                    select(show_session_events.c.created_at).where(show_session_events.c.id == after_id)
                ).scalar_one_or_none()
                if anchor is not None:
                    query = query.where(
                        (show_session_events.c.created_at > anchor)
                        | (
                            (show_session_events.c.created_at == anchor)
                            & (show_session_events.c.id > after_id)
                        )
                    )
            query = query.order_by(show_session_events.c.created_at.asc(), show_session_events.c.id.asc()).limit(
                effective_limit
            )
            rows = [_row_to_payload(dict(row)) for row in conn.execute(query).mappings().all()]
        return {
            "events": rows,
            "next_after_id": rows[-1]["id"] if len(rows) == effective_limit else None,
        }


def _validate_event_type(raw: Any) -> str:
    event_type = str(raw or "").strip()
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise ShowSessionEventError(f"Unsupported show event type: {event_type}", code="unsupported_event_type")
    return event_type


def _actor_for_event(event_type: str) -> str:
    return "assistant" if event_type.startswith("assistant.") else "human"


def _normalize_event_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event_type == "assistant.mark.created":
        mark = _normalize_json_object(payload.get("mark") or payload.get("payload"))
        target = _required_text(mark.get("target"), "mark.target")
        body = _required_text(mark.get("body") or mark.get("comment"), "mark.body")
        return {
            "id": _text_or_none(mark.get("id")) or _new_id("mark"),
            "role": "assistant",
            "scope": _text_or_none(mark.get("scope")) or DEFAULT_MARK_SCOPE,
            "target": target,
            "body": body,
            "createdAt": _text_or_none(mark.get("createdAt")) or _utc_now_iso(),
        }
    return _normalize_json_object(payload.get("payload") or payload)


def _normalize_json_object(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _event_scope(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "assistant.mark.created":
        return _text_or_none(payload.get("scope")) or DEFAULT_MARK_SCOPE
    return _text_or_none(payload.get("scope")) or DEFAULT_MARK_SCOPE


def _event_id(original_payload: dict[str, Any], event_payload: dict[str, Any]) -> str:
    return (
        _text_or_none(original_payload.get("id"))
        or _text_or_none(event_payload.get("id"))
        or _new_id("show_evt")
    )


def _event_created_at(event_payload: dict[str, Any]) -> str:
    return _text_or_none(event_payload.get("createdAt")) or _text_or_none(event_payload.get("created_at")) or _utc_now_iso()


def _format_transcript_text(event_type: str, payload: dict[str, Any], anchor: dict[str, Any]) -> str:
    if event_type == "assistant.mark.created":
        lines = [
            f"[agent-mark:{payload.get('scope') or DEFAULT_MARK_SCOPE}] {payload.get('target')}",
            "",
            str(payload.get("body") or "").strip(),
        ]
        selector = _text_or_none(anchor.get("selector"))
        if selector:
            lines.extend(["", f"Anchor: {selector}"])
        text = _text_or_none(anchor.get("text"))
        if text:
            lines.append(f"Text: {text}")
        return "\n".join(lines)

    label = "annotation" if event_type == "human.annotation.created" else "intent"
    text = _text_or_none(payload.get("text") or payload.get("comment") or payload.get("value"))
    return f"[show-{label}:{payload.get('scope') or DEFAULT_MARK_SCOPE}] {text or _json_dumps(payload)}"


def _row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "type": row["event_type"],
        "actor": row["actor"],
        "scope": row["scope"],
        "anchor": _json_loads(row.get("anchor_json"), {}),
        "payload": _json_loads(row.get("payload_json"), {}),
        "transcript_text": row.get("transcript_text"),
        "message_id": row.get("message_id"),
        "created_at": row.get("created_at"),
    }


def _required_text(raw: Any, field: str) -> str:
    value = _text_or_none(raw)
    if not value:
        raise ShowSessionEventError(f"{field} is required.", code="invalid_payload")
    return value


def _text_or_none(raw: Any) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback
