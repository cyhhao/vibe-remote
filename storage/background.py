from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import insert, select, update

from config import paths
from storage.db import SqliteInvalidationProbe, create_sqlite_engine
from storage.migrations import background_tables_ready, initialize_background_tables
from storage.models import background_runs, background_tasks

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
                select(background_tasks)
                .where(background_tasks.c.task_type == "scheduled")
                .where(background_tasks.c.deleted_at.is_(None))
                .order_by(background_tasks.c.created_at, background_tasks.c.id)
            ).mappings()
            return [self._scheduled_task_from_row(row) for row in rows]

    def get_scheduled_task(self, task_id: str) -> Optional[dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(background_tasks)
                .where(background_tasks.c.task_type == "scheduled")
                .where(background_tasks.c.id == task_id)
                .where(background_tasks.c.deleted_at.is_(None))
                .limit(1)
            ).mappings().first()
            return self._scheduled_task_from_row(row) if row else None

    def upsert_scheduled_task(self, payload: dict[str, Any]) -> None:
        values = self._scheduled_task_values(payload)
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(background_tasks.c.id).where(background_tasks.c.id == values["id"]).limit(1)
            ).scalar_one_or_none()
            if existing:
                conn.execute(update(background_tasks).where(background_tasks.c.id == values["id"]).values(**values))
            else:
                conn.execute(insert(background_tasks).values(**values))

    def remove_task(self, task_id: str, *, deleted_at: Optional[str] = None) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                update(background_tasks)
                .where(background_tasks.c.id == task_id)
                .where(background_tasks.c.deleted_at.is_(None))
                .values(deleted_at=deleted_at or _utc_now_iso())
            )
            return bool(result.rowcount)

    def list_watches(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(background_tasks)
                .where(background_tasks.c.task_type == "watch")
                .where(background_tasks.c.deleted_at.is_(None))
                .order_by(background_tasks.c.created_at, background_tasks.c.id)
            ).mappings()
            return [self._watch_from_row(row) for row in rows]

    def get_watch(self, watch_id: str) -> Optional[dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(background_tasks)
                .where(background_tasks.c.task_type == "watch")
                .where(background_tasks.c.id == watch_id)
                .where(background_tasks.c.deleted_at.is_(None))
                .limit(1)
            ).mappings().first()
            return self._watch_from_row(row) if row else None

    def upsert_watch(self, payload: dict[str, Any]) -> None:
        values = self._watch_values(payload)
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(background_tasks.c.id).where(background_tasks.c.id == values["id"]).limit(1)
            ).scalar_one_or_none()
            if existing:
                conn.execute(update(background_tasks).where(background_tasks.c.id == values["id"]).values(**values))
            else:
                conn.execute(insert(background_tasks).values(**values))

    def enqueue_run(self, payload: dict[str, Any]) -> None:
        values = self._run_values(payload)
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(background_runs.c.id).where(background_runs.c.id == values["id"]).limit(1)
            ).scalar_one_or_none()
            if existing:
                conn.execute(update(background_runs).where(background_runs.c.id == values["id"]).values(**values))
            else:
                conn.execute(insert(background_runs).values(**values))

    def list_runs(self, *, status: Optional[str] = None) -> list[dict[str, Any]]:
        stmt = select(background_runs)
        if status:
            stmt = stmt.where(background_runs.c.status == status)
        stmt = stmt.order_by(background_runs.c.created_at, background_runs.c.id)
        with self.engine.connect() as conn:
            return [self._run_from_row(row) for row in conn.execute(stmt).mappings()]

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(select(background_runs).where(background_runs.c.id == run_id).limit(1)).mappings().first()
            return self._run_from_row(row) if row else None

    def claim_pending_run(self, run_id: str, *, started_at: str) -> Optional[dict[str, Any]]:
        with self.engine.begin() as conn:
            result = conn.execute(
                update(background_runs)
                .where(background_runs.c.id == run_id)
                .where(background_runs.c.status == "pending")
                .values(status="processing", started_at=started_at, updated_at=started_at)
            )
            if not result.rowcount:
                return None
            row = conn.execute(select(background_runs).where(background_runs.c.id == run_id).limit(1)).mappings().first()
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
        task_id: Optional[str] = None,
        session_key: Optional[str] = None,
        session_id: Optional[str] = None,
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
        if task_id is not None:
            values["task_id"] = task_id
        if session_key is not None:
            values["legacy_session_key"] = session_key
        if session_id is not None:
            values["session_id"] = session_id
        if metadata is not None:
            values["metadata_json"] = _json_dumps(metadata)
        with self.engine.begin() as conn:
            conn.execute(update(background_runs).where(background_runs.c.id == run_id).values(**values))

    def recover_processing_runs(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(background_runs)
                .where(background_runs.c.status == "processing")
                .values(status="pending", started_at=None, pid=None)
            )

    def write_watch_runtime(self, payload: dict[str, Any], *, updated_at: str) -> None:
        watches = payload.get("watches", {}) if isinstance(payload, dict) else {}
        with self.engine.begin() as conn:
            conn.execute(
                update(background_runs)
                .where(background_runs.c.run_type == "watch_runtime")
                .where(background_runs.c.status.in_(["running", "pending"]))
                .values(status="completed", completed_at=updated_at, updated_at=updated_at)
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
                        "task_id": watch_id,
                        "pid": runtime_payload.get("pid"),
                        "created_at": runtime_payload.get("started_at") or updated_at,
                        "started_at": runtime_payload.get("started_at"),
                        "updated_at": runtime_payload.get("updated_at") or updated_at,
                        "metadata": runtime_payload,
                    }
                )
                existing = conn.execute(
                    select(background_runs.c.id).where(background_runs.c.id == run_id).limit(1)
                ).scalar_one_or_none()
                if existing:
                    conn.execute(update(background_runs).where(background_runs.c.id == run_id).values(**values))
                else:
                    conn.execute(insert(background_runs).values(**values))

    def load_watch_runtime(self) -> dict[str, Any]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(background_runs)
                .where(background_runs.c.run_type == "watch_runtime")
                .where(background_runs.c.status == "running")
            ).mappings()
            watches: dict[str, Any] = {}
            for row in rows:
                payload = _json_loads(row["metadata_json"], {})
                watch_id = row["task_id"]
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
            "task_type": "scheduled",
            "name": payload.get("name"),
            "session_id": payload.get("session_id"),
            "legacy_session_key": payload.get("session_key") or None,
            "prompt": payload.get("prompt") or "",
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
            "last_error": payload.get("last_error"),
            "last_exit_code": None,
            "metadata_json": _json_dumps({}),
        }

    def _watch_values(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": payload["id"],
            "task_type": "watch",
            "name": payload.get("name"),
            "session_id": payload.get("session_id"),
            "legacy_session_key": payload.get("session_key") or None,
            "prompt": None,
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
        return {
            "id": payload["id"],
            "task_id": payload.get("task_id"),
            "run_type": payload.get("request_type") or payload.get("run_type") or "hook_send",
            "status": payload.get("status") or "pending",
            "session_id": payload.get("session_id"),
            "legacy_session_key": payload.get("session_key") or payload.get("legacy_session_key"),
            "post_to": payload.get("post_to"),
            "deliver_key": payload.get("deliver_key"),
            "prompt": payload.get("prompt"),
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
            "session_key": row["legacy_session_key"] or "",
            "session_id": row["session_id"],
            "prompt": row["prompt"] or "",
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
            "last_error": row["last_error"],
        }

    @staticmethod
    def _watch_from_row(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "session_key": row["legacy_session_key"] or "",
            "session_id": row["session_id"],
            "command": _json_loads(row["command_json"], []),
            "shell_command": row["shell_command"],
            "prefix": row["prefix"],
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
            "status": row["status"],
            "task_id": row["task_id"],
            "session_key": row["legacy_session_key"],
            "session_id": row["session_id"],
            "post_to": row["post_to"],
            "deliver_key": row["deliver_key"],
            "prompt": row["prompt"],
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
            "ok": None if row["completed_at"] is None else row["status"] == "completed",
        }
