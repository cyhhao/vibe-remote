from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.scheduled_tasks import ScheduledTaskService, ScheduledTaskStore, parse_session_key


class _StubScheduler:
    def __init__(self) -> None:
        self.jobs = {}
        self.started = False
        self.shutdown_calls = 0

    def start(self) -> None:
        self.started = True

    def shutdown(self, wait: bool = False) -> None:
        self.shutdown_calls += 1

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def add_job(self, func, trigger, id, replace_existing, coalesce, max_instances, args):
        self.jobs[id] = SimpleNamespace(id=id, trigger=trigger, args=args)

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)

    def get_jobs(self):
        return list(self.jobs.values())


def test_parse_session_key_accepts_channel_and_thread() -> None:
    parsed = parse_session_key("slack::channel::C123::thread::171717.123")

    assert parsed.platform == "slack"
    assert parsed.scope_type == "channel"
    assert parsed.scope_id == "C123"
    assert parsed.thread_id == "171717.123"


def test_parse_session_key_rejects_invalid_scope_type() -> None:
    try:
        parse_session_key("slack::room::C123")
    except ValueError as exc:
        assert "scope type" in str(exc)
    else:
        raise AssertionError("expected invalid scope type to raise ValueError")


def test_store_round_trip_persists_task(tmp_path: Path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    task = store.add_task(
        session_key="discord::channel::123",
        prompt="send digest",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )

    reloaded = ScheduledTaskStore(store.path)
    payload = json.loads(store.path.read_text(encoding="utf-8"))

    assert payload["tasks"][0]["id"] == task.id
    assert reloaded.get_task(task.id) is not None
    assert reloaded.get_task(task.id).session_key == "discord::channel::123"


def test_store_reload_detects_deleted_task_file(tmp_path: Path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    store.add_task(
        session_key="slack::channel::C123",
        prompt="send digest",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )

    assert store.list_tasks()
    store.path.unlink()

    assert store.maybe_reload() is True
    assert store.list_tasks() == []


def test_mark_task_result_skips_deleted_task_after_reload(tmp_path: Path) -> None:
    path = tmp_path / "scheduled_tasks.json"
    writer = ScheduledTaskStore(path)
    task = writer.add_task(
        session_key="slack::channel::C123",
        prompt="send digest",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )
    remover = ScheduledTaskStore(path)
    assert remover.remove_task(task.id) is True

    updated = writer.mark_task_result(task.id, error="boom")
    reloaded = ScheduledTaskStore(path)

    assert updated is False
    assert reloaded.get_task(task.id) is None


def test_service_rejects_unsupported_platform_at_runtime() -> None:
    controller = SimpleNamespace(platform_settings_managers={"slack": object()})
    service = ScheduledTaskService(controller=controller, store=ScheduledTaskStore(Path("/tmp/nonexistent-scheduled.json")))

    try:
        service.validate_platform("foo")
    except ValueError as exc:
        assert "unsupported task platform" in str(exc)
    else:
        raise AssertionError("expected unsupported platform to raise ValueError")


def test_build_context_assigns_unique_scheduled_message_ids() -> None:
    settings_manager = SimpleNamespace(get_store=lambda: SimpleNamespace(get_user=lambda *_args, **_kwargs: None))
    controller = SimpleNamespace(platform_settings_managers={"slack": settings_manager})
    service = ScheduledTaskService(controller=controller, store=ScheduledTaskStore(Path("/tmp/nonexistent-scheduled.json")))
    target = parse_session_key("slack::channel::C123")

    first = asyncio.run(service._build_context(target, task_id="task-1"))
    second = asyncio.run(service._build_context(target, task_id="task-1"))

    assert first.message_id.startswith("scheduled:task-1:")
    assert second.message_id.startswith("scheduled:task-1:")
    assert first.message_id != second.message_id


def test_run_task_records_scheduled_handler_error(tmp_path: Path) -> None:
    path = tmp_path / "scheduled_tasks.json"
    store = ScheduledTaskStore(path)
    task = store.add_task(
        session_key="slack::channel::C123",
        prompt="send digest",
        schedule_type="at",
        run_at="2026-03-31T09:00:00+08:00",
        timezone_name="Asia/Shanghai",
    )
    settings_manager = SimpleNamespace(get_store=lambda: SimpleNamespace(get_user=lambda *_args, **_kwargs: None))

    async def _handle_scheduled_message(context, message, parsed_session_key=None):
        return "scheduled turn failed"

    controller = SimpleNamespace(
        platform_settings_managers={"slack": settings_manager},
        message_handler=SimpleNamespace(handle_scheduled_message=_handle_scheduled_message),
    )
    service = ScheduledTaskService(controller=controller, store=store)

    asyncio.run(service._run_task(task.id))
    reloaded = ScheduledTaskStore(path)
    updated = reloaded.get_task(task.id)

    assert updated is not None
    assert updated.last_error == "scheduled turn failed"
    assert updated.enabled is False


def test_reconcile_jobs_skips_invalid_tasks_and_keeps_valid_jobs(tmp_path: Path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    valid = store.add_task(
        session_key="slack::channel::C123",
        prompt="send digest",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )
    invalid = store.add_task(
        session_key="slack::channel::C123",
        prompt="broken digest",
        schedule_type="cron",
        cron="not-a-cron",
        timezone_name="Asia/Shanghai",
    )
    controller = SimpleNamespace(platform_settings_managers={})
    service = ScheduledTaskService(controller=controller, store=store)
    service.scheduler = _StubScheduler()

    service.reconcile_jobs()

    assert valid.id in service.scheduler.jobs
    assert invalid.id not in service.scheduler.jobs


def test_start_keeps_watcher_alive_after_initial_reconcile_failure(tmp_path: Path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    controller = SimpleNamespace(platform_settings_managers={})
    service = ScheduledTaskService(controller=controller, store=store)
    service.scheduler = _StubScheduler()

    async def _watch_store():
        await asyncio.Event().wait()

    def _fail_once():
        raise ValueError("bad trigger")

    service._watch_store = _watch_store  # type: ignore[method-assign]
    service.reconcile_jobs = _fail_once  # type: ignore[method-assign]

    async def _exercise():
        service.start()
        assert service._running is True
        assert service._reconcile_task is not None
        service._reconcile_task.cancel()
        try:
            await service._reconcile_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_exercise())
