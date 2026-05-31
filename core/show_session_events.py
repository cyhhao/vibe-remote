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
SUPPORTED_EVENT_TYPES = {
    "assistant.mark.created",
    "assistant.mark.updated",
    "assistant.mark.resolved",
    "assistant.page.updated",
    "human.intent.submitted",
    "human.annotation.created",
    "human.annotation.updated",
    "human.annotation.resolved",
    "human.annotation.dismissed",
    "system.runtime.status",
    "system.runtime.error",
}
ANNOTATION_EVENT_TYPES = {
    "human.annotation.created",
    "human.annotation.updated",
    "human.annotation.resolved",
    "human.annotation.dismissed",
}


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
        anchor = _normalize_json_object(payload.get("anchor") or event_payload.get("anchor"))
        scope = _event_scope(event_type, event_payload)
        transcript_text = _format_transcript_text(event_type, event_payload, anchor)
        event_id = _event_id(payload, event_payload)
        created_at = _utc_now_iso()

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
                    author="agent" if actor in {"assistant", "system"} else "user",
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
    if event_type.startswith("assistant."):
        return "assistant"
    if event_type.startswith("system."):
        return "system"
    return "human"


def _normalize_event_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event_type.startswith("assistant.mark."):
        mark = _normalize_json_object(payload.get("mark") or payload.get("payload"))
        target = _required_text(mark.get("target"), "mark.target")
        body = _required_text(mark.get("body") or mark.get("comment"), "mark.body")
        created_at = _text_or_none(mark.get("createdAt")) or _utc_now_iso()
        return {
            "id": _text_or_none(mark.get("id")) or _new_id("mark"),
            "role": "assistant",
            "scope": _text_or_none(mark.get("scope")) or DEFAULT_MARK_SCOPE,
            "target": target,
            "body": body,
            "status": "resolved" if event_type == "assistant.mark.resolved" else _text_or_none(mark.get("status")) or "active",
            "createdAt": created_at,
            "updatedAt": _text_or_none(mark.get("updatedAt")) or created_at,
            "resolvedAt": _text_or_none(mark.get("resolvedAt")) if event_type != "assistant.mark.resolved" else _text_or_none(mark.get("resolvedAt")) or _utc_now_iso(),
        }
    if event_type == "human.intent.submitted":
        intent_payload = _normalize_json_object(payload.get("payload") or payload)
        created_at = _text_or_none(intent_payload.get("createdAt")) or _utc_now_iso()
        normalized = dict(intent_payload)
        normalized.setdefault("id", _new_id("intent"))
        normalized["scope"] = _text_or_none(normalized.get("scope")) or DEFAULT_MARK_SCOPE
        normalized["createdAt"] = created_at
        return normalized
    if event_type in ANNOTATION_EVENT_TYPES:
        annotation = _normalize_json_object(payload.get("annotation") or payload.get("payload") or payload)
        created_at = _text_or_none(annotation.get("createdAt")) or _utc_now_iso()
        normalized = dict(annotation)
        normalized.setdefault("id", _new_id("annotation"))
        normalized["scope"] = _text_or_none(normalized.get("scope")) or DEFAULT_MARK_SCOPE
        normalized["status"] = _annotation_status_for_event(event_type, _text_or_none(normalized.get("status")))
        normalized["createdAt"] = created_at
        normalized["updatedAt"] = _text_or_none(normalized.get("updatedAt")) or created_at
        if event_type == "human.annotation.resolved":
            normalized["resolvedAt"] = _text_or_none(normalized.get("resolvedAt")) or _utc_now_iso()
        return normalized
    normalized = _normalize_json_object(payload.get("payload") or payload)
    if not normalized:
        normalized = {}
    normalized.setdefault("id", _new_id("runtime" if event_type.startswith("system.") else "page"))
    normalized.setdefault("createdAt", _utc_now_iso())
    normalized.setdefault("scope", DEFAULT_MARK_SCOPE)
    return normalized


def _normalize_json_object(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _event_scope(event_type: str, payload: dict[str, Any]) -> str:
    if event_type.startswith("assistant.mark."):
        return _text_or_none(payload.get("scope")) or DEFAULT_MARK_SCOPE
    return _text_or_none(payload.get("scope")) or DEFAULT_MARK_SCOPE


def _event_id(original_payload: dict[str, Any], event_payload: dict[str, Any]) -> str:
    return _text_or_none(original_payload.get("id")) or _new_id("show_evt")


def _format_transcript_text(event_type: str, payload: dict[str, Any], anchor: dict[str, Any]) -> str:
    if event_type.startswith("assistant.mark."):
        action = event_type.split(".")[-1]
        lines = [
            f"[agent-mark:{payload.get('scope') or DEFAULT_MARK_SCOPE}:{action}] {payload.get('target')}",
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

    if event_type == "human.intent.submitted":
        text = _text_or_none(payload.get("text") or payload.get("comment") or payload.get("value"))
        label = _text_or_none(payload.get("intent") or payload.get("component")) or "intent"
        return f"[show-intent:{payload.get('scope') or DEFAULT_MARK_SCOPE}] {label}\n\n{text or _json_dumps(payload)}"

    if event_type in ANNOTATION_EVENT_TYPES:
        action = event_type.split(".")[-1]
        text = _text_or_none(payload.get("text") or payload.get("comment"))
        label = _text_or_none(payload.get("intent")) or "comment"
        lines = [f"[show-annotation:{payload.get('scope') or DEFAULT_MARK_SCOPE}:{action}] {label}"]
        if text:
            lines.extend(["", text])
        quote = _text_or_none(anchor.get("textQuote") or anchor.get("text"))
        if quote:
            lines.extend(["", f"Quote: {quote}"])
        selector = _text_or_none(anchor.get("selector"))
        if selector:
            lines.append(f"Anchor: {selector}")
        return "\n".join(lines)

    if event_type == "assistant.page.updated":
        summary = _text_or_none(payload.get("summary") or payload.get("text") or payload.get("body"))
        return f"[show-page-updated] {summary or _json_dumps(payload)}"

    if event_type == "system.runtime.error":
        text = _text_or_none(payload.get("error") or payload.get("message") or payload.get("status"))
        return f"[show-runtime-error] {text or _json_dumps(payload)}"

    if event_type == "system.runtime.status":
        return ""

    return _json_dumps(payload)


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


def _annotation_status_for_event(event_type: str, requested: str | None) -> str:
    if event_type == "human.annotation.resolved":
        return "resolved"
    if event_type == "human.annotation.dismissed":
        return "dismissed"
    return requested or "pending"


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
