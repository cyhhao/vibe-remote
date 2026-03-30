"""Scheduled task persistence, parsing, and runtime orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from config import paths
from modules.im import MessageContext

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ParsedSessionKey:
    platform: str
    scope_type: str
    scope_id: str
    thread_id: Optional[str] = None

    @property
    def session_scope(self) -> str:
        return f"{self.platform}::{self.scope_id}"

    @property
    def is_dm(self) -> bool:
        return self.scope_type == "user"

    def to_key(self) -> str:
        base = f"{self.platform}::{self.scope_type}::{self.scope_id}"
        if self.thread_id:
            return f"{base}::thread::{self.thread_id}"
        return base


def parse_session_key(value: str) -> ParsedSessionKey:
    raw = (value or "").strip()
    parts = raw.split("::") if raw else []
    if len(parts) not in {3, 5}:
        raise ValueError("session key must be '<platform>::<channel|user>::<id>[::thread::<thread_id>]'")

    platform, scope_type, scope_id = parts[:3]
    if not platform or not scope_id:
        raise ValueError("session key platform and scope id are required")
    if scope_type not in {"channel", "user"}:
        raise ValueError("session key scope type must be 'channel' or 'user'")

    thread_id: Optional[str] = None
    if len(parts) == 5:
        if parts[3] != "thread" or not parts[4]:
            raise ValueError("session key thread segment must be '::thread::<thread_id>'")
        thread_id = parts[4]

    return ParsedSessionKey(
        platform=platform,
        scope_type=scope_type,
        scope_id=scope_id,
        thread_id=thread_id,
    )


@dataclass
class ScheduledTask:
    id: str
    session_key: str
    prompt: str
    schedule_type: str
    cron: Optional[str] = None
    run_at: Optional[str] = None
    timezone: str = "UTC"
    enabled: bool = True
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    last_run_at: Optional[str] = None
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ScheduledTask":
        return cls(
            id=str(payload.get("id") or uuid4().hex[:12]),
            session_key=str(payload.get("session_key") or ""),
            prompt=str(payload.get("prompt") or ""),
            schedule_type=str(payload.get("schedule_type") or ""),
            cron=payload.get("cron"),
            run_at=payload.get("run_at"),
            timezone=str(payload.get("timezone") or "UTC"),
            enabled=bool(payload.get("enabled", True)),
            created_at=str(payload.get("created_at") or _utc_now_iso()),
            updated_at=str(payload.get("updated_at") or _utc_now_iso()),
            last_run_at=payload.get("last_run_at"),
            last_error=payload.get("last_error"),
        )


class ScheduledTaskStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or (paths.get_state_dir() / "scheduled_tasks.json")
        self._mtime: float = 0
        self._tasks: Dict[str, ScheduledTask] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._tasks = {}
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to load scheduled tasks: %s", exc)
            self._tasks = {}
            return

        raw_tasks = payload.get("tasks", []) if isinstance(payload, dict) else []
        tasks: Dict[str, ScheduledTask] = {}
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            task = ScheduledTask.from_dict(item)
            tasks[task.id] = task
        self._tasks = tasks
        try:
            self._mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self._mtime = 0

    def maybe_reload(self) -> bool:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            mtime = 0
        if mtime <= self._mtime:
            return False
        self.load()
        return True

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tasks": [task.to_dict() for task in self.list_tasks()]}
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=self.path.parent,
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as handle:
            json.dump(payload, handle, indent=2)
            tmp_path = Path(handle.name)
        tmp_path.replace(self.path)
        self._mtime = self.path.stat().st_mtime

    def list_tasks(self) -> list[ScheduledTask]:
        return sorted(self._tasks.values(), key=lambda item: (item.created_at, item.id))

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        return self._tasks.get(task_id)

    def upsert_task(self, task: ScheduledTask) -> ScheduledTask:
        task.updated_at = _utc_now_iso()
        self._tasks[task.id] = task
        self._save()
        return task

    def add_task(
        self,
        *,
        session_key: str,
        prompt: str,
        schedule_type: str,
        cron: Optional[str] = None,
        run_at: Optional[str] = None,
        timezone_name: str,
    ) -> ScheduledTask:
        task = ScheduledTask(
            id=uuid4().hex[:12],
            session_key=session_key,
            prompt=prompt,
            schedule_type=schedule_type,
            cron=cron,
            run_at=run_at,
            timezone=timezone_name,
        )
        return self.upsert_task(task)

    def remove_task(self, task_id: str) -> bool:
        if task_id not in self._tasks:
            return False
        del self._tasks[task_id]
        self._save()
        return True

    def set_enabled(self, task_id: str, enabled: bool) -> ScheduledTask:
        task = self._tasks[task_id]
        task.enabled = enabled
        task.updated_at = _utc_now_iso()
        self._save()
        return task

    def mark_task_result(self, task_id: str, *, error: Optional[str]) -> None:
        task = self._tasks[task_id]
        task.last_run_at = _utc_now_iso()
        task.last_error = error
        if task.schedule_type == "at":
            task.enabled = False
        task.updated_at = _utc_now_iso()
        self._save()


class ScheduledTaskService:
    """Controller-owned runtime that executes persisted scheduled tasks."""

    def __init__(self, controller, store: Optional[ScheduledTaskStore] = None):
        self.controller = controller
        self.store = store or ScheduledTaskStore()
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self._reconcile_task: Optional[asyncio.Task] = None
        self._job_signatures: Dict[str, tuple[Any, ...]] = {}
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self.scheduler.start()
        self._running = True
        self.reconcile_jobs()
        self._reconcile_task = asyncio.create_task(self._watch_store())

    async def stop(self) -> None:
        self._running = False
        if self._reconcile_task:
            self._reconcile_task.cancel()
            try:
                await self._reconcile_task
            except asyncio.CancelledError:
                pass
            self._reconcile_task = None
        self.scheduler.shutdown(wait=False)

    async def _watch_store(self) -> None:
        while self._running:
            try:
                if self.store.maybe_reload():
                    self.reconcile_jobs()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Scheduled task store watch failed: %s", exc, exc_info=True)
            await asyncio.sleep(2)

    def reconcile_jobs(self) -> None:
        desired_ids = set()
        for task in self.store.list_tasks():
            if not task.enabled:
                continue
            desired_ids.add(task.id)
            signature = (
                task.schedule_type,
                task.cron,
                task.run_at,
                task.timezone,
                task.session_key,
                task.prompt,
                task.enabled,
            )
            if self._job_signatures.get(task.id) == signature and self.scheduler.get_job(task.id):
                continue
            if self.scheduler.get_job(task.id):
                self.scheduler.remove_job(task.id)
            self.scheduler.add_job(
                self._run_task,
                trigger=self._build_trigger(task),
                id=task.id,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                args=[task.id],
            )
            self._job_signatures[task.id] = signature

        for job in list(self.scheduler.get_jobs()):
            if job.id not in desired_ids:
                self.scheduler.remove_job(job.id)
                self._job_signatures.pop(job.id, None)

    def _build_trigger(self, task: ScheduledTask):
        tz = ZoneInfo(task.timezone)
        if task.schedule_type == "cron":
            if not task.cron:
                raise ValueError(f"scheduled task {task.id} is missing cron expression")
            return CronTrigger.from_crontab(task.cron, timezone=tz)
        if task.schedule_type == "at":
            if not task.run_at:
                raise ValueError(f"scheduled task {task.id} is missing run_at timestamp")
            return DateTrigger(run_date=datetime.fromisoformat(task.run_at).astimezone(tz))
        raise ValueError(f"unknown schedule type: {task.schedule_type}")

    async def _run_task(self, task_id: str) -> None:
        self.store.maybe_reload()
        task = self.store.get_task(task_id)
        if not task or not task.enabled:
            return

        error: Optional[str] = None
        try:
            target = parse_session_key(task.session_key)
            context = await self._build_context(target)
            await self.controller.message_handler.handle_scheduled_message(
                context=context,
                message=task.prompt,
                parsed_session_key=target,
            )
        except Exception as exc:
            error = str(exc)
            logger.error("Scheduled task %s failed: %s", task_id, exc, exc_info=True)
        finally:
            self.store.mark_task_result(task_id, error=error)
            self.reconcile_jobs()

    async def _build_context(self, target: ParsedSessionKey) -> MessageContext:
        platform = target.platform
        settings_manager = self.controller.platform_settings_managers[platform]

        channel_id = target.scope_id
        user_id = "scheduled"
        if target.is_dm:
            user_id = target.scope_id
            bound_user = settings_manager.get_store().get_user(target.scope_id, platform=platform)
            if platform == "lark":
                dm_chat_id = getattr(bound_user, "dm_chat_id", "") if bound_user else ""
                if not dm_chat_id:
                    raise ValueError(f"lark user {target.scope_id} is missing dm_chat_id binding")
                channel_id = dm_chat_id
            elif bound_user and getattr(bound_user, "dm_chat_id", ""):
                channel_id = bound_user.dm_chat_id

        return MessageContext(
            user_id=user_id,
            channel_id=channel_id,
            platform=platform,
            thread_id=target.thread_id,
            platform_specific={
                "platform": platform,
                "is_dm": target.is_dm,
                "turn_source": "scheduled",
                "session_key_external": target.to_key(),
            },
        )
