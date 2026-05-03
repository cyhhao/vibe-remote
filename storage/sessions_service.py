from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, select

from config.v2_sessions import ActivePollInfo, SessionState
from config.v2_settings import _split_scoped_key
from storage.db import SqliteInvalidationProbe, create_sqlite_engine
from storage.models import agent_sessions, runtime_records, scopes, state_meta
from storage.settings_service import make_scope_id, upsert_scope

SESSIONS_LAST_ACTIVITY_KEY = "sessions_last_activity"
JSON_VALUE_PREFIX = "__json__:"
SESSION_ID_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"


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
            used_session_ids: set[str] = set()

            for scope_key, agent_maps in state.session_mappings.items():
                if not isinstance(agent_maps, dict):
                    continue
                scope_id = resolve_scope_from_legacy_key(conn, str(scope_key), now=now)
                for agent_name, thread_map in agent_maps.items():
                    if not isinstance(thread_map, dict):
                        continue
                    for thread_id, native_session_id in thread_map.items():
                        thread_key = str(thread_id)
                        conn.execute(
                            agent_sessions.insert().values(
                                id=_new_session_id(used_session_ids),
                                scope_id=scope_id,
                                agent_backend=_agent_backend(str(agent_name)),
                                agent_variant=str(agent_name) or "default",
                                model=None,
                                reasoning_effort=None,
                                session_anchor=thread_key,
                                workdir=_workdir_from_anchor(thread_key),
                                native_session_id=encode_session_value(native_session_id),
                                title=None,
                                status="active",
                                metadata_json=_json_dumps({"legacy_scope_key": str(scope_key)}),
                                created_at=now,
                                updated_at=now,
                                last_active_at=now,
                            )
                        )

            for scope_key, channel_map in state.active_slack_threads.items():
                if not isinstance(channel_map, dict):
                    continue
                scope_id = resolve_scope_from_legacy_key(conn, str(scope_key), now=now)
                for channel_id, thread_map in channel_map.items():
                    if not isinstance(thread_map, dict):
                        continue
                    for thread_id, last_active_at in thread_map.items():
                        record_key = f"{scope_key}|{channel_id}|{thread_id}"
                        conn.execute(
                            runtime_records.insert().values(
                                id=f"runtime::active_thread::{record_key}",
                                record_type="active_thread",
                                record_key=record_key,
                                scope_id=scope_id,
                                session_anchor=str(thread_id),
                                workdir=None,
                                payload_json=_json_dumps(
                                    {
                                        "scope_key": str(scope_key),
                                        "channel_id": str(channel_id),
                                        "thread_id": str(thread_id),
                                        "last_active_at": _float(last_active_at),
                                    }
                                ),
                                expires_at=None,
                                created_at=now,
                                updated_at=now,
                            )
                        )

            for opencode_session_id, item in state.active_polls.items():
                data = item.to_dict() if isinstance(item, ActivePollInfo) else item
                if not isinstance(data, dict):
                    continue
                record_key = str(opencode_session_id)
                settings_key = str(data.get("settings_key") or "")
                platform = str(data.get("platform") or "")
                scope_id = resolve_scope_from_legacy_key(
                    conn,
                    f"{platform}::{settings_key}" if platform and "::" not in settings_key else settings_key,
                    now=now,
                )
                conn.execute(
                    runtime_records.insert().values(
                        id=f"runtime::active_poll::{record_key}",
                        record_type="active_poll",
                        record_key=record_key,
                        scope_id=scope_id,
                        session_anchor=str(data.get("base_session_id") or data.get("thread_id") or ""),
                        workdir=str(data.get("working_path") or "") or None,
                        payload_json=_json_dumps(data),
                        expires_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                )

            seen_messages: set[tuple[str, str, str]] = set()
            for channel_id, thread_map in state.processed_message_ts.items():
                if not isinstance(thread_map, dict):
                    continue
                for thread_id, value in thread_map.items():
                    message_ids = [value] if isinstance(value, str) else list(value or [])
                    for message_id in message_ids[-200:]:
                        key = (str(channel_id), str(thread_id), str(message_id))
                        if key in seen_messages:
                            continue
                        seen_messages.add(key)
                        record_key = "|".join(key)
                        conn.execute(
                            runtime_records.insert().values(
                                id=f"runtime::processed_message::{record_key}",
                                record_type="processed_message",
                                record_key=record_key,
                                scope_id=None,
                                session_anchor=str(thread_id),
                                workdir=None,
                                payload_json=_json_dumps(
                                    {
                                        "channel_id": str(channel_id),
                                        "thread_id": str(thread_id),
                                        "message_id": str(message_id),
                                        "processed_at": now,
                                    }
                                ),
                                expires_at=None,
                                created_at=now,
                                updated_at=now,
                            )
                        )

            if state.last_activity is not None:
                conn.execute(
                    state_meta.insert().values(
                        key=SESSIONS_LAST_ACTIVITY_KEY,
                        value_json=_json_dumps(state.last_activity),
                        updated_at=now,
                    )
                )

    def _clear(self, conn: Connection) -> None:
        conn.execute(agent_sessions.delete())
        conn.execute(runtime_records.delete())
        conn.execute(state_meta.delete().where(state_meta.c.key == SESSIONS_LAST_ACTIVITY_KEY))

    def _load_session_mappings(self, conn: Connection) -> dict[str, dict[str, dict[str, Any]]]:
        rows = conn.execute(
            select(
                agent_sessions.c.scope_id,
                agent_sessions.c.agent_variant,
                agent_sessions.c.session_anchor,
                agent_sessions.c.native_session_id,
                agent_sessions.c.metadata_json,
                scopes.c.platform,
                scopes.c.native_id,
            ).join(scopes, scopes.c.id == agent_sessions.c.scope_id, isouter=True)
        ).mappings()
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            scope_key = _legacy_scope_key(row)
            agent_name = str(row["agent_variant"] or "default")
            result.setdefault(scope_key, {}).setdefault(agent_name, {})[str(row["session_anchor"])] = (
                decode_session_value(row["native_session_id"])
            )
        return result

    def _load_active_threads(self, conn: Connection) -> dict[str, dict[str, dict[str, float]]]:
        rows = conn.execute(
            select(runtime_records.c.payload_json).where(runtime_records.c.record_type == "active_thread")
        )
        result: dict[str, dict[str, dict[str, float]]] = {}
        for (payload_json,) in rows:
            payload = _json_loads(payload_json, {})
            scope_key = str(payload.get("scope_key") or "")
            channel_id = str(payload.get("channel_id") or "")
            thread_id = str(payload.get("thread_id") or "")
            if not scope_key or not channel_id or not thread_id:
                continue
            result.setdefault(scope_key, {}).setdefault(channel_id, {})[thread_id] = _float(
                payload.get("last_active_at")
            )
        return result

    def _load_active_polls(self, conn: Connection) -> dict[str, dict[str, Any]]:
        rows = conn.execute(
            select(runtime_records.c.record_key, runtime_records.c.payload_json).where(
                runtime_records.c.record_type == "active_poll"
            )
        )
        result: dict[str, dict[str, Any]] = {}
        for record_key, payload_json in rows:
            payload = _json_loads(payload_json, {})
            if not isinstance(payload, dict):
                continue
            payload.setdefault("opencode_session_id", str(record_key))
            result[str(record_key)] = payload
        return result

    def _load_processed_messages(self, conn: Connection) -> dict[str, dict[str, list[str]]]:
        rows = conn.execute(
            select(runtime_records.c.payload_json)
            .where(runtime_records.c.record_type == "processed_message")
            .order_by(runtime_records.c.created_at)
        )
        result: dict[str, dict[str, list[str]]] = {}
        for (payload_json,) in rows:
            payload = _json_loads(payload_json, {})
            channel_id = str(payload.get("channel_id") or "")
            thread_id = str(payload.get("thread_id") or "")
            message_id = str(payload.get("message_id") or "")
            if not channel_id or not thread_id or not message_id:
                continue
            result.setdefault(channel_id, {}).setdefault(thread_id, []).append(message_id)
        return result

    def _load_last_activity(self, conn: Connection) -> str | None:
        value = conn.execute(
            select(state_meta.c.value_json).where(state_meta.c.key == SESSIONS_LAST_ACTIVITY_KEY)
        ).scalar_one_or_none()
        return _json_loads(value, None)


def resolve_scope_from_legacy_key(conn: Connection, scope_key: str, *, now: str) -> str | None:
    platform, native_id = _split_scoped_key(scope_key)
    if not native_id:
        return None
    if platform is None:
        platform = "unknown"
    existing = conn.execute(
        select(scopes.c.id).where(scopes.c.platform == platform, scopes.c.native_id == native_id).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return str(existing)
    return upsert_scope(conn, platform, _infer_scope_type(platform, native_id), native_id, now=now)


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


def _legacy_scope_key(row: dict[str, Any]) -> str:
    metadata = _json_loads(row.get("metadata_json"), {})
    if isinstance(metadata, dict) and metadata.get("legacy_scope_key"):
        return str(metadata["legacy_scope_key"])
    platform = row.get("platform")
    native_id = row.get("native_id")
    if platform and native_id:
        return f"{platform}::{native_id}"
    scope_id = row.get("scope_id")
    if isinstance(scope_id, str) and scope_id.count("::") >= 2:
        parts = scope_id.split("::", 2)
        return f"{parts[0]}::{parts[2]}"
    return str(scope_id or "")


def _infer_scope_type(platform: str, native_id: str) -> str:
    if platform == "slack" and native_id and native_id[0] in {"U", "W"}:
        return "user"
    if platform == "lark" and native_id.startswith("ou_"):
        return "user"
    if platform == "wechat" and (native_id.startswith("wxid_") or native_id.startswith("user")):
        return "user"
    return "channel"


def _agent_backend(agent_name: str) -> str:
    return agent_name if agent_name in {"codex", "claude", "opencode"} else "unknown"


def _new_session_id(used: set[str]) -> str:
    while True:
        value = "ses" + "".join(secrets.choice(SESSION_ID_ALPHABET) for _ in range(10))
        if value not in used:
            used.add(value)
            return value


def _workdir_from_anchor(anchor: str) -> str | None:
    if ":" not in anchor:
        return None
    suffix = anchor.rsplit(":", 1)[1]
    return suffix or None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
