from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, select

from config.v2_sessions import ActivePollInfo, SessionState
from storage.db import SqliteInvalidationProbe, create_sqlite_engine
from storage.models import (
    active_polls,
    active_threads,
    agent_session_bindings,
    processed_messages,
    schema_meta,
)

SESSIONS_LAST_ACTIVITY_KEY = "sessions_last_activity"
JSON_VALUE_PREFIX = "__json__:"


class SQLiteSessionsService:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.engine = create_sqlite_engine(db_path)
        self._probe = SqliteInvalidationProbe(self.engine)

    def close(self) -> None:
        self._probe.close()
        self.engine.dispose()

    def has_external_write(self) -> bool:
        return self._probe.has_external_write()

    def load_state(self) -> SessionState:
        with self.engine.connect() as conn:
            return SessionState(
                session_mappings=self._load_session_mappings(conn),
                active_slack_threads=self._load_active_threads(conn),
                active_polls=self._load_active_polls(conn),
                processed_message_ts=self._load_processed_messages(conn),
                last_activity=self._load_last_activity(conn),
            )

    def save_state(self, state: SessionState) -> None:
        with self.engine.begin() as conn:
            self._clear(conn)
            now = _utc_now_iso()

            for scope_key, agent_maps in state.session_mappings.items():
                if not isinstance(agent_maps, dict):
                    continue
                for agent_name, thread_map in agent_maps.items():
                    if not isinstance(thread_map, dict):
                        continue
                    for thread_id, session_id in thread_map.items():
                        conn.execute(
                            agent_session_bindings.insert().values(
                                scope_key=str(scope_key),
                                agent_name=str(agent_name),
                                thread_id=str(thread_id),
                                session_id=encode_session_value(session_id),
                                created_at=now,
                                updated_at=now,
                            )
                        )

            for scope_key, channel_map in state.active_slack_threads.items():
                if not isinstance(channel_map, dict):
                    continue
                for channel_id, thread_map in channel_map.items():
                    if not isinstance(thread_map, dict):
                        continue
                    for thread_id, last_active_at in thread_map.items():
                        conn.execute(
                            active_threads.insert().values(
                                scope_key=str(scope_key),
                                channel_id=str(channel_id),
                                thread_id=str(thread_id),
                                last_active_at=_float(last_active_at),
                            )
                        )

            for opencode_session_id, item in state.active_polls.items():
                data = item.to_dict() if isinstance(item, ActivePollInfo) else item
                if not isinstance(data, dict):
                    continue
                conn.execute(
                    active_polls.insert().values(
                        opencode_session_id=str(opencode_session_id),
                        base_session_id=str(data.get("base_session_id") or ""),
                        platform=str(data.get("platform") or ""),
                        channel_id=str(data.get("channel_id") or ""),
                        thread_id=str(data.get("thread_id") or ""),
                        settings_key=str(data.get("settings_key") or ""),
                        working_path=str(data.get("working_path") or ""),
                        started_at=_float(data.get("started_at")),
                        baseline_message_ids_json=_json_dumps(data.get("baseline_message_ids") or []),
                        seen_tool_calls_json=_json_dumps(data.get("seen_tool_calls") or []),
                        emitted_assistant_messages_json=_json_dumps(data.get("emitted_assistant_messages") or []),
                        ack_reaction_message_id=data.get("ack_reaction_message_id"),
                        ack_reaction_emoji=data.get("ack_reaction_emoji"),
                        typing_indicator_active=_bool_int(data.get("typing_indicator_active", False)),
                        context_token=str(data.get("context_token") or ""),
                        processing_indicator_json=_json_dumps(data.get("processing_indicator") or {}),
                        user_id=str(data.get("user_id") or ""),
                        updated_at=now,
                    )
                )

            for channel_id, thread_map in state.processed_message_ts.items():
                if not isinstance(thread_map, dict):
                    continue
                for thread_id, value in thread_map.items():
                    message_ids = [value] if isinstance(value, str) else list(value or [])
                    for message_id in message_ids[-200:]:
                        conn.execute(
                            processed_messages.insert().values(
                                channel_id=str(channel_id),
                                thread_id=str(thread_id),
                                message_id=str(message_id),
                                processed_at=now,
                            ).prefix_with("OR IGNORE")
                        )

            if state.last_activity is not None:
                conn.execute(
                    schema_meta.insert().values(
                        key=SESSIONS_LAST_ACTIVITY_KEY,
                        value=str(state.last_activity),
                        updated_at=now,
                    )
                )

    def _clear(self, conn: Connection) -> None:
        conn.execute(agent_session_bindings.delete())
        conn.execute(active_threads.delete())
        conn.execute(active_polls.delete())
        conn.execute(processed_messages.delete())
        conn.execute(schema_meta.delete().where(schema_meta.c.key == SESSIONS_LAST_ACTIVITY_KEY))

    def _load_session_mappings(self, conn: Connection) -> dict[str, dict[str, dict[str, Any]]]:
        rows = conn.execute(
            select(
                agent_session_bindings.c.scope_key,
                agent_session_bindings.c.agent_name,
                agent_session_bindings.c.thread_id,
                agent_session_bindings.c.session_id,
            )
        )
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for scope_key, agent_name, thread_id, session_id in rows:
            result.setdefault(str(scope_key), {}).setdefault(str(agent_name), {})[str(thread_id)] = decode_session_value(
                session_id
            )
        return result

    def _load_active_threads(self, conn: Connection) -> dict[str, dict[str, dict[str, float]]]:
        rows = conn.execute(
            select(
                active_threads.c.scope_key,
                active_threads.c.channel_id,
                active_threads.c.thread_id,
                active_threads.c.last_active_at,
            )
        )
        result: dict[str, dict[str, dict[str, float]]] = {}
        for scope_key, channel_id, thread_id, last_active_at in rows:
            result.setdefault(str(scope_key), {}).setdefault(str(channel_id), {})[str(thread_id)] = float(last_active_at)
        return result

    def _load_active_polls(self, conn: Connection) -> dict[str, dict[str, Any]]:
        rows = conn.execute(select(active_polls)).mappings()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            session_id = str(row["opencode_session_id"])
            result[session_id] = {
                "opencode_session_id": session_id,
                "base_session_id": row["base_session_id"] or "",
                "platform": row["platform"] or "",
                "channel_id": row["channel_id"] or "",
                "thread_id": row["thread_id"] or "",
                "settings_key": row["settings_key"] or "",
                "working_path": row["working_path"] or "",
                "baseline_message_ids": _json_loads(row["baseline_message_ids_json"], []),
                "seen_tool_calls": _json_loads(row["seen_tool_calls_json"], []),
                "emitted_assistant_messages": _json_loads(row["emitted_assistant_messages_json"], []),
                "started_at": float(row["started_at"] or 0.0),
                "ack_reaction_message_id": row["ack_reaction_message_id"],
                "ack_reaction_emoji": row["ack_reaction_emoji"],
                "typing_indicator_active": bool(row["typing_indicator_active"]),
                "context_token": row["context_token"] or "",
                "processing_indicator": _json_loads(row["processing_indicator_json"], {}),
                "user_id": row["user_id"] or "",
            }
        return result

    def _load_processed_messages(self, conn: Connection) -> dict[str, dict[str, list[str]]]:
        rows = conn.execute(
            select(
                processed_messages.c.channel_id,
                processed_messages.c.thread_id,
                processed_messages.c.message_id,
            ).order_by(processed_messages.c.id)
        )
        result: dict[str, dict[str, list[str]]] = {}
        for channel_id, thread_id, message_id in rows:
            thread_map = result.setdefault(str(channel_id), {})
            items = thread_map.setdefault(str(thread_id), [])
            items.append(str(message_id))
        return result

    def _load_last_activity(self, conn: Connection) -> str | None:
        return conn.execute(
            select(schema_meta.c.value).where(schema_meta.c.key == SESSIONS_LAST_ACTIVITY_KEY)
        ).scalar_one_or_none()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def encode_session_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return JSON_VALUE_PREFIX + _json_dumps(value)


def decode_session_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if not value.startswith(JSON_VALUE_PREFIX):
        return value
    try:
        return json.loads(value[len(JSON_VALUE_PREFIX) :])
    except (TypeError, ValueError):
        return value


def _bool_int(value: Any) -> int:
    return 1 if bool(value) else 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
