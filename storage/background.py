from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import insert, or_, select, update

from config import paths
from storage.db import SqliteInvalidationProbe, create_sqlite_engine
from storage.migrations import background_tables_ready, initialize_background_tables
from storage.models import agent_runs, run_definitions
from storage.pagination import PageRequest, PageResult, page_result_from_limit_plus_one

logger = logging.getLogger(__name__)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


RUN_STATUS_ALIASES: dict[str, str] = {
    "pending": "queued",
    "queued": "queued",
    "processing": "running",
    "running": "running",
    "completed": "succeeded",
    "succeeded": "succeeded",
    "failed": "failed",
    "canceled": "canceled",
}


def normalize_run_status(status: Any) -> str:
    return RUN_STATUS_ALIASES.get(str(status or "").strip(), str(status or "").strip() or "queued")


def _status_query_values(status: str) -> list[str]:
    normalized = normalize_run_status(status)
    values = [raw for raw, public in RUN_STATUS_ALIASES.items() if public == normalized]
    return values or [normalized]


class SQLiteBackgroundTaskStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or paths.get_sqlite_state_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if db_path is None:
            from storage.importer import ensure_sqlite_state, resolve_primary_platform_from_config

            ensure_sqlite_state(primary_platform=resolve_primary_platform_from_config(paths.get_state_dir()))
        if not background_tables_ready(self.db_path):
            initialize_background_tables(self.db_path)
        self.engine = create_sqlite_engine(self.db_path)
        self._probe = SqliteInvalidationProbe(self.engine)

    def close(self) -> None:
        self._probe.close()
        self.engine.dispose()

    def maybe_reload(self) -> bool:
        return self._probe.has_external_write()

    def list_scheduled_tasks(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(run_definitions)
                .where(run_definitions.c.definition_type == "scheduled")
                .where(run_definitions.c.deleted_at.is_(None))
                .order_by(run_definitions.c.created_at, run_definitions.c.id)
            ).mappings()
            return [self._scheduled_task_from_row(row) for row in rows]

    def get_scheduled_task(self, definition_id: str) -> Optional[dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(run_definitions)
                .where(run_definitions.c.definition_type == "scheduled")
                .where(run_definitions.c.id == definition_id)
                .where(run_definitions.c.deleted_at.is_(None))
                .limit(1)
            ).mappings().first()
            return self._scheduled_task_from_row(row) if row else None

    def upsert_scheduled_task(self, payload: dict[str, Any]) -> None:
        values = self._scheduled_task_values(payload)
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(run_definitions.c.id).where(run_definitions.c.id == values["id"]).limit(1)
            ).scalar_one_or_none()
            if existing:
                conn.execute(update(run_definitions).where(run_definitions.c.id == values["id"]).values(**values))
            else:
                conn.execute(insert(run_definitions).values(**values))

    def remove_task(self, definition_id: str, *, deleted_at: Optional[str] = None) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                update(run_definitions)
                .where(run_definitions.c.id == definition_id)
                .where(run_definitions.c.deleted_at.is_(None))
                .values(deleted_at=deleted_at or _utc_now_iso())
            )
            return bool(result.rowcount)

    def list_watches(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(run_definitions)
                .where(run_definitions.c.definition_type == "watch")
                .where(run_definitions.c.deleted_at.is_(None))
                .order_by(run_definitions.c.created_at, run_definitions.c.id)
            ).mappings()
            return [self._watch_from_row(row) for row in rows]

    def get_watch(self, watch_id: str) -> Optional[dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(run_definitions)
                .where(run_definitions.c.definition_type == "watch")
                .where(run_definitions.c.id == watch_id)
                .where(run_definitions.c.deleted_at.is_(None))
                .limit(1)
            ).mappings().first()
            return self._watch_from_row(row) if row else None

    def upsert_watch(self, payload: dict[str, Any]) -> None:
        values = self._watch_values(payload)
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(run_definitions.c.id).where(run_definitions.c.id == values["id"]).limit(1)
            ).scalar_one_or_none()
            if existing:
                conn.execute(update(run_definitions).where(run_definitions.c.id == values["id"]).values(**values))
            else:
                conn.execute(insert(run_definitions).values(**values))

    def enqueue_run(self, payload: dict[str, Any]) -> None:
        values = self._run_values(payload)
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(agent_runs.c.id).where(agent_runs.c.id == values["id"]).limit(1)
            ).scalar_one_or_none()
            if existing:
                conn.execute(update(agent_runs).where(agent_runs.c.id == values["id"]).values(**values))
            else:
                conn.execute(insert(agent_runs).values(**values))

    def list_runs(self, *, status: Optional[str] = None) -> list[dict[str, Any]]:
        stmt = self._runs_query(status=status).order_by(agent_runs.c.created_at, agent_runs.c.id)
        with self.engine.connect() as conn:
            return [self._run_from_row(row) for row in conn.execute(stmt).mappings()]

    def list_runs_page(
        self,
        *,
        status: Optional[str] = None,
        run_type: Optional[str] = None,
        agent_name: Optional[str] = None,
        agent_backend: Optional[str] = None,
        session_id: Optional[str] = None,
        definition_id: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        query: Optional[str] = None,
        page_request: PageRequest | None,
        newest_first: bool = True,
    ) -> PageResult[dict[str, Any]]:
        stmt = self._runs_query(
            status=status,
            run_type=run_type,
            agent_name=agent_name,
            agent_backend=agent_backend,
            session_id=session_id,
            definition_id=definition_id,
            created_after=created_after,
            created_before=created_before,
            query=query,
        )
        if newest_first:
            stmt = stmt.order_by(agent_runs.c.created_at.desc(), agent_runs.c.id.desc())
        else:
            stmt = stmt.order_by(agent_runs.c.created_at, agent_runs.c.id)
        if page_request is not None:
            stmt = stmt.offset(page_request.offset).limit(page_request.limit + 1)
        with self.engine.connect() as conn:
            rows = [self._run_from_row(row) for row in conn.execute(stmt).mappings()]
        return page_result_from_limit_plus_one(rows, page_request)

    def _runs_query(
        self,
        *,
        status: Optional[str] = None,
        run_type: Optional[str] = None,
        agent_name: Optional[str] = None,
        agent_backend: Optional[str] = None,
        session_id: Optional[str] = None,
        definition_id: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        query: Optional[str] = None,
    ):
        stmt = select(agent_runs)
        if status:
            stmt = stmt.where(agent_runs.c.status.in_(_status_query_values(status)))
        if run_type:
            stmt = stmt.where(agent_runs.c.run_type == run_type)
        if agent_name:
            stmt = stmt.where(agent_runs.c.agent_name == agent_name)
        if agent_backend:
            stmt = stmt.where(agent_runs.c.agent_backend == agent_backend)
        if session_id:
            stmt = stmt.where(agent_runs.c.session_id == session_id)
        if definition_id:
            stmt = stmt.where(agent_runs.c.definition_id == definition_id)
        if created_after:
            stmt = stmt.where(agent_runs.c.created_at >= created_after)
        if created_before:
            stmt = stmt.where(agent_runs.c.created_at <= created_before)
        if query:
            pattern = f"%{query}%"
            stmt = stmt.where(
                or_(
                    agent_runs.c.id.like(pattern),
                    agent_runs.c.definition_id.like(pattern),
                    agent_runs.c.agent_name.like(pattern),
                    agent_runs.c.session_id.like(pattern),
                    agent_runs.c.prompt.like(pattern),
                    agent_runs.c.message.like(pattern),
                    agent_runs.c.result_text.like(pattern),
                    agent_runs.c.error.like(pattern),
                    agent_runs.c.stdout.like(pattern),
                    agent_runs.c.stderr.like(pattern),
                )
            )
        return stmt

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(select(agent_runs).where(agent_runs.c.id == run_id).limit(1)).mappings().first()
            return self._run_from_row(row) if row else None

    def cancel_run(self, run_id: str, *, requested_at: Optional[str] = None) -> bool:
        now = requested_at or _utc_now_iso()
        with self.engine.begin() as conn:
            row = conn.execute(select(agent_runs.c.status).where(agent_runs.c.id == run_id).limit(1)).mappings().first()
            if not row:
                return False
            status = normalize_run_status(row["status"])
            values: dict[str, Any] = {
                "cancel_requested": 1,
                "cancel_requested_at": now,
                "updated_at": now,
            }
            if status == "queued":
                values["status"] = "canceled"
                values["completed_at"] = now
            result = conn.execute(
                update(agent_runs)
                .where(agent_runs.c.id == run_id)
                .values(**values)
            )
            return bool(result.rowcount)

    def claim_pending_run(self, run_id: str, *, started_at: str) -> Optional[dict[str, Any]]:
        with self.engine.begin() as conn:
            row = conn.execute(select(agent_runs).where(agent_runs.c.id == run_id).limit(1)).mappings().first()
            if not row:
                return None
            if bool(row["cancel_requested"]) or normalize_run_status(row["status"]) == "canceled":
                conn.execute(
                    update(agent_runs)
                    .where(agent_runs.c.id == run_id)
                    .values(status="canceled", completed_at=started_at, updated_at=started_at)
                )
                return None
            result = conn.execute(
                update(agent_runs)
                .where(agent_runs.c.id == run_id)
                .where(agent_runs.c.status.in_(_status_query_values("queued")))
                .values(status="running", started_at=started_at, updated_at=started_at)
            )
            if not result.rowcount:
                return None
            row = conn.execute(select(agent_runs).where(agent_runs.c.id == run_id).limit(1)).mappings().first()
            return self._run_from_row(row) if row else None

    def update_run_status(
        self,
        run_id: str,
        *,
        status: str,
        updated_at: str,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        exit_code: Optional[int] = None,
        error: Optional[str] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        pid: Optional[int] = None,
        definition_id: Optional[str] = None,
        task_id: Optional[str] = None,
        session_key: Optional[str] = None,
        session_id: Optional[str] = None,
        result_text: Optional[str] = None,
        result_payload: Optional[dict[str, Any]] = None,
        message_ids: Optional[list[str]] = None,
        cancel_requested: Optional[bool] = None,
        cancel_requested_at: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "updated_at": updated_at,
        }
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        if exit_code is not None:
            values["exit_code"] = exit_code
        if error is not None:
            values["error"] = error
        if stdout is not None:
            values["stdout"] = stdout
        if stderr is not None:
            values["stderr"] = stderr
        if pid is not None:
            values["pid"] = pid
        resolved_definition_id = definition_id or task_id
        if resolved_definition_id is not None:
            values["definition_id"] = resolved_definition_id
        if session_key is not None:
            values["legacy_session_key"] = session_key
        if session_id is not None:
            values["session_id"] = session_id
        if result_text is not None:
            values["result_text"] = result_text
        if result_payload is not None:
            values["result_payload_json"] = _json_dumps(result_payload)
        if message_ids is not None:
            values["message_ids_json"] = _json_dumps(message_ids)
        if cancel_requested is not None:
            values["cancel_requested"] = 1 if cancel_requested else 0
        if cancel_requested_at is not None:
            values["cancel_requested_at"] = cancel_requested_at
        if metadata is not None:
            values["metadata_json"] = _json_dumps(metadata)
        with self.engine.begin() as conn:
            conn.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(**values))

    def record_run_message(
        self,
        run_id: str,
        *,
        text: str,
        message_id: str | None = None,
        updated_at: Optional[str] = None,
    ) -> None:
        now = updated_at or _utc_now_iso()
        with self.engine.begin() as conn:
            row = conn.execute(select(agent_runs).where(agent_runs.c.id == run_id).limit(1)).mappings().first()
            if not row:
                return
            existing_text = str(row["result_text"] or "")
            incoming = str(text or "")
            if existing_text and incoming:
                result_text = f"{existing_text}\n\n{incoming}"
            else:
                result_text = existing_text or incoming
            message_ids = _json_loads(row["message_ids_json"], [])
            if message_id:
                message_ids.append(message_id)
            conn.execute(
                update(agent_runs)
                .where(agent_runs.c.id == run_id)
                .values(
                    result_text=result_text,
                    message_ids_json=_json_dumps(message_ids),
                    updated_at=now,
                )
            )

    def recover_processing_runs(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(agent_runs)
                .where(agent_runs.c.status.in_(_status_query_values("running")))
                .where(agent_runs.c.run_type != "watch_runtime")
                .values(status="queued", started_at=None, pid=None)
            )

    def write_watch_runtime(self, payload: dict[str, Any], *, updated_at: str) -> None:
        watches = payload.get("watches", {}) if isinstance(payload, dict) else {}
        with self.engine.begin() as conn:
            conn.execute(
                update(agent_runs)
                .where(agent_runs.c.run_type == "watch_runtime")
                .where(agent_runs.c.status.in_(_status_query_values("running") + _status_query_values("queued")))
                .values(status="succeeded", completed_at=updated_at, updated_at=updated_at)
            )
            for watch_id, runtime_payload in watches.items():
                if not isinstance(runtime_payload, dict):
                    continue
                run_id = f"runtime:{watch_id}"
                values = self._run_values(
                    {
                        "id": run_id,
                        "request_type": "watch_runtime",
                        "status": "running" if runtime_payload.get("running") else "completed",
                        "definition_id": watch_id,
                        "pid": runtime_payload.get("pid"),
                        "created_at": runtime_payload.get("started_at") or updated_at,
                        "started_at": runtime_payload.get("started_at"),
                        "updated_at": runtime_payload.get("updated_at") or updated_at,
                        "metadata": runtime_payload,
                    }
                )
                existing = conn.execute(
                    select(agent_runs.c.id).where(agent_runs.c.id == run_id).limit(1)
                ).scalar_one_or_none()
                if existing:
                    conn.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(**values))
                else:
                    conn.execute(insert(agent_runs).values(**values))

    def load_watch_runtime(self) -> dict[str, Any]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(agent_runs)
                .where(agent_runs.c.run_type == "watch_runtime")
                .where(agent_runs.c.status == "running")
            ).mappings()
            watches: dict[str, Any] = {}
            for row in rows:
                payload = _json_loads(row["metadata_json"], {})
                watch_id = row["definition_id"]
                if watch_id:
                    watches[str(watch_id)] = {
                        "running": True,
                        "pid": row["pid"],
                        "started_at": row["started_at"],
                        "updated_at": row["updated_at"],
                    } | (payload if isinstance(payload, dict) else {})
            return {"watches": watches}

    def _scheduled_task_values(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": payload["id"],
            "definition_type": "scheduled",
            "name": payload.get("name"),
            "agent_name": payload.get("agent_name"),
            "session_policy": payload.get("session_policy") or ("existing" if payload.get("session_id") or payload.get("session_key") else None),
            "session_id": payload.get("session_id"),
            "legacy_session_key": payload.get("session_key") or None,
            "prompt": payload.get("prompt") or payload.get("message") or "",
            "message": payload.get("message") or payload.get("prompt") or "",
            "message_payload_json": self._message_payload_json(payload),
            "schedule_type": payload.get("schedule_type") or "",
            "cron": payload.get("cron"),
            "run_at": payload.get("run_at"),
            "timezone": payload.get("timezone") or "UTC",
            "command_json": None,
            "shell_command": None,
            "prefix": None,
            "cwd": None,
            "mode": None,
            "timeout_seconds": None,
            "lifetime_timeout_seconds": None,
            "retry_exit_codes_json": None,
            "retry_delay_seconds": None,
            "post_to": payload.get("post_to"),
            "deliver_key": payload.get("deliver_key"),
            "enabled": 1 if payload.get("enabled", True) else 0,
            "deleted_at": payload.get("deleted_at"),
            "created_at": payload.get("created_at") or payload.get("updated_at"),
            "updated_at": payload.get("updated_at") or payload.get("created_at"),
            "last_started_at": None,
            "last_finished_at": None,
            "last_event_at": None,
            "last_run_at": payload.get("last_run_at"),
            "last_run_id": payload.get("last_run_id"),
            "last_error": payload.get("last_error"),
            "last_exit_code": None,
            "metadata_json": _json_dumps({}),
        }

    def _watch_values(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": payload["id"],
            "definition_type": "watch",
            "name": payload.get("name"),
            "agent_name": payload.get("agent_name"),
            "session_policy": payload.get("session_policy") or ("existing" if payload.get("session_id") or payload.get("session_key") else None),
            "session_id": payload.get("session_id"),
            "legacy_session_key": payload.get("session_key") or None,
            "prompt": None,
            "message": payload.get("message") or payload.get("prefix"),
            "message_payload_json": self._message_payload_json(payload),
            "schedule_type": None,
            "cron": None,
            "run_at": None,
            "timezone": None,
            "command_json": _json_dumps(payload.get("command") or []),
            "shell_command": payload.get("shell_command"),
            "prefix": payload.get("prefix"),
            "cwd": payload.get("cwd"),
            "mode": payload.get("mode") or "once",
            "timeout_seconds": float(payload.get("timeout_seconds", 21600.0)),
            "lifetime_timeout_seconds": float(payload.get("lifetime_timeout_seconds", 0.0)),
            "retry_exit_codes_json": _json_dumps(payload.get("retry_exit_codes") or []),
            "retry_delay_seconds": float(payload.get("retry_delay_seconds", 30.0)),
            "post_to": payload.get("post_to"),
            "deliver_key": payload.get("deliver_key"),
            "enabled": 1 if payload.get("enabled", True) else 0,
            "deleted_at": payload.get("deleted_at"),
            "created_at": payload.get("created_at") or payload.get("updated_at"),
            "updated_at": payload.get("updated_at") or payload.get("created_at"),
            "last_started_at": payload.get("last_started_at"),
            "last_finished_at": payload.get("last_finished_at"),
            "last_event_at": payload.get("last_event_at"),
            "last_run_at": None,
            "last_error": payload.get("last_error"),
            "last_exit_code": payload.get("last_exit_code"),
            "metadata_json": _json_dumps({}),
        }

    def _run_values(self, payload: dict[str, Any]) -> dict[str, Any]:
        created_at = payload.get("created_at") or payload.get("updated_at")
        message = payload.get("message") or payload.get("prompt")
        return {
            "id": payload["id"],
            "definition_id": payload.get("definition_id") or payload.get("task_id"),
            "run_type": payload.get("request_type") or payload.get("run_type") or "hook_send",
            "status": normalize_run_status(payload.get("status")),
            "source_kind": payload.get("source_kind"),
            "source_actor": payload.get("source_actor"),
            "parent_run_id": payload.get("parent_run_id"),
            "agent_name": payload.get("agent_name"),
            "agent_id": payload.get("agent_id"),
            "agent_backend": payload.get("agent_backend"),
            "model": payload.get("model") or payload.get("agent_model"),
            "reasoning_effort": payload.get("reasoning_effort") or payload.get("agent_reasoning_effort"),
            "session_policy": payload.get("session_policy"),
            "session_id": payload.get("session_id"),
            "legacy_session_key": payload.get("session_key") or payload.get("legacy_session_key"),
            "post_to": payload.get("post_to"),
            "deliver_key": payload.get("deliver_key"),
            "prompt": payload.get("prompt") or message,
            "message": message,
            "message_payload_json": self._message_payload_json(payload),
            "result_text": payload.get("result_text"),
            "result_payload_json": self._payload_json(payload, "result_payload", "result_payload_json"),
            "message_ids_json": self._payload_json(payload, "message_ids", "message_ids_json"),
            "cancel_requested": 1 if payload.get("cancel_requested") else 0,
            "cancel_requested_at": payload.get("cancel_requested_at"),
            "pid": payload.get("pid"),
            "exit_code": payload.get("exit_code"),
            "error": payload.get("error"),
            "stdout": payload.get("stdout"),
            "stderr": payload.get("stderr"),
            "created_at": created_at,
            "started_at": payload.get("started_at"),
            "completed_at": payload.get("completed_at"),
            "updated_at": payload.get("updated_at") or created_at,
            "metadata_json": _json_dumps(payload.get("metadata") or {}),
        }

    @staticmethod
    def _scheduled_task_from_row(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "agent_name": row["agent_name"],
            "session_policy": row["session_policy"],
            "session_key": row["legacy_session_key"] or "",
            "session_id": row["session_id"],
            "prompt": row["prompt"] or "",
            "message": row["message"] or row["prompt"] or "",
            "message_payload": _json_loads(row["message_payload_json"], None),
            "schedule_type": row["schedule_type"] or "",
            "post_to": row["post_to"],
            "deliver_key": row["deliver_key"],
            "cron": row["cron"],
            "run_at": row["run_at"],
            "timezone": row["timezone"] or "UTC",
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_run_at": row["last_run_at"],
            "last_run_id": row["last_run_id"],
            "last_error": row["last_error"],
        }

    @staticmethod
    def _watch_from_row(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "agent_name": row["agent_name"],
            "session_policy": row["session_policy"],
            "session_key": row["legacy_session_key"] or "",
            "session_id": row["session_id"],
            "command": _json_loads(row["command_json"], []),
            "shell_command": row["shell_command"],
            "prefix": row["prefix"],
            "message": row["message"] or row["prefix"],
            "message_payload": _json_loads(row["message_payload_json"], None),
            "cwd": row["cwd"],
            "mode": row["mode"] or "once",
            "timeout_seconds": float(row["timeout_seconds"] or 21600.0),
            "lifetime_timeout_seconds": float(row["lifetime_timeout_seconds"] or 0.0),
            "retry_exit_codes": [int(code) for code in _json_loads(row["retry_exit_codes_json"], [])],
            "retry_delay_seconds": float(row["retry_delay_seconds"] or 30.0),
            "post_to": row["post_to"],
            "deliver_key": row["deliver_key"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_started_at": row["last_started_at"],
            "last_finished_at": row["last_finished_at"],
            "last_event_at": row["last_event_at"],
            "last_error": row["last_error"],
            "last_exit_code": row["last_exit_code"],
        }

    @staticmethod
    def _run_from_row(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "request_type": row["run_type"],
            "run_type": row["run_type"],
            "status": normalize_run_status(row["status"]),
            "definition_id": row["definition_id"],
            "task_id": row["definition_id"],
            "source_kind": row["source_kind"],
            "source_actor": row["source_actor"],
            "parent_run_id": row["parent_run_id"],
            "agent_name": row["agent_name"],
            "agent_id": row["agent_id"],
            "agent_backend": row["agent_backend"],
            "model": row["model"],
            "reasoning_effort": row["reasoning_effort"],
            "session_policy": row["session_policy"],
            "session_key": row["legacy_session_key"],
            "session_id": row["session_id"],
            "post_to": row["post_to"],
            "deliver_key": row["deliver_key"],
            "prompt": row["prompt"],
            "message": row["message"] or row["prompt"],
            "message_payload": _json_loads(row["message_payload_json"], None),
            "result_text": row["result_text"],
            "result_payload": _json_loads(row["result_payload_json"], None),
            "message_ids": _json_loads(row["message_ids_json"], []),
            "cancel_requested": bool(row["cancel_requested"]),
            "cancel_requested_at": row["cancel_requested_at"],
            "pid": row["pid"],
            "exit_code": row["exit_code"],
            "error": row["error"],
            "stdout": row["stdout"],
            "stderr": row["stderr"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "updated_at": row["updated_at"],
            "metadata": _json_loads(row["metadata_json"], {}),
            "ok": None if row["completed_at"] is None else normalize_run_status(row["status"]) == "succeeded",
        }

    @staticmethod
    def _message_payload_json(payload: dict[str, Any]) -> Optional[str]:
        return SQLiteBackgroundTaskStore._payload_json(payload, "message_payload", "message_payload_json")

    @staticmethod
    def _payload_json(payload: dict[str, Any], object_key: str, json_key: str) -> Optional[str]:
        if payload.get(json_key) is not None:
            return payload.get(json_key)
        if payload.get(object_key) is not None:
            return _json_dumps(payload.get(object_key))
        return None
