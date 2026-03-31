from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.scheduled_tasks import (
    ScheduledTaskService,
    ScheduledTaskStore,
    TaskExecutionRequest,
    TaskExecutionStore,
    build_session_key_for_context,
    parse_session_key,
)
from modules.im import MessageContext


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


def test_build_session_key_for_context_defaults_to_threadless_scope() -> None:
    context = MessageContext(
        user_id="U123",
        channel_id="C123",
        platform="slack",
        thread_id="171717.123",
        platform_specific={"is_dm": False},
    )

    parsed = build_session_key_for_context(context)

    assert parsed.to_key(include_thread=False) == "slack::channel::C123"
    assert parsed.thread_id is None


def test_build_session_key_for_context_uses_fallback_platform() -> None:
    context = MessageContext(
        user_id="U123",
        channel_id="C123",
        thread_id="171717.123",
        platform_specific={"is_dm": False},
    )

    parsed = build_session_key_for_context(context, fallback_platform="slack")

    assert parsed.to_key(include_thread=False) == "slack::channel::C123"


def test_store_round_trip_persists_task(tmp_path: Path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    task = store.add_task(
        session_key="discord::channel::123",
        post_to="channel",
        deliver_key="discord::channel::456",
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
    assert reloaded.get_task(task.id).post_to == "channel"
    assert reloaded.get_task(task.id).deliver_key == "discord::channel::456"


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
    controller = SimpleNamespace(
        platform_settings_managers={"slack": settings_manager},
        get_im_client_for_context=lambda _context: SimpleNamespace(
            should_use_thread_for_reply=lambda: True,
            should_use_thread_for_dm_session=lambda: False,
        ),
    )
    service = ScheduledTaskService(controller=controller, store=ScheduledTaskStore(Path("/tmp/nonexistent-scheduled.json")))
    target = parse_session_key("slack::channel::C123")

    first = asyncio.run(service._build_context(target, execution_id="exec-1", task_id="task-1"))
    second = asyncio.run(service._build_context(target, execution_id="exec-2", task_id="task-1"))

    assert first.message_id.startswith("scheduled:task-1:")
    assert second.message_id.startswith("scheduled:task-1:")
    assert first.message_id != second.message_id


def test_build_context_assigns_hook_message_id() -> None:
    settings_manager = SimpleNamespace(get_store=lambda: SimpleNamespace(get_user=lambda *_args, **_kwargs: None))
    controller = SimpleNamespace(
        platform_settings_managers={"slack": settings_manager},
        get_im_client_for_context=lambda _context: SimpleNamespace(
            should_use_thread_for_reply=lambda: True,
            should_use_thread_for_dm_session=lambda: False,
        ),
    )
    service = ScheduledTaskService(controller=controller, store=ScheduledTaskStore(Path("/tmp/nonexistent-scheduled.json")))
    target = parse_session_key("slack::channel::C123")

    context = asyncio.run(service._build_context(target, execution_id="exec-hook", trigger_kind="hook"))

    assert context.message_id == "hook:exec-hook"
    assert context.platform_specific["task_trigger_kind"] == "hook"


def test_build_context_separates_delivery_target_from_session_target() -> None:
    settings_manager = SimpleNamespace(get_store=lambda: SimpleNamespace(get_user=lambda *_args, **_kwargs: None))
    controller = SimpleNamespace(
        platform_settings_managers={"slack": settings_manager},
        get_im_client_for_context=lambda _context: SimpleNamespace(
            should_use_thread_for_reply=lambda: True,
            should_use_thread_for_dm_session=lambda: False,
        ),
    )
    service = ScheduledTaskService(controller=controller, store=ScheduledTaskStore(Path("/tmp/nonexistent-scheduled.json")))
    session_target = parse_session_key("slack::channel::C123::thread::171717.123")
    delivery_target = parse_session_key("slack::channel::C123")

    context = asyncio.run(
        service._build_context(
            session_target,
            delivery_target=delivery_target,
            execution_id="exec-1",
            task_id="task-1",
        )
    )

    assert context.thread_id == "171717.123"
    assert context.platform_specific["delivery_override"]["thread_id"] is None
    assert context.platform_specific["delivery_scope_session_key"] == "slack::C123"
    assert context.platform_specific["scheduled_delivery_alias"]["mode"] == "sent_message"
    assert context.platform_specific["scheduled_delivery_alias"]["clear_source"] is False


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


def test_request_store_enqueue_claim_and_complete(tmp_path: Path) -> None:
    store = TaskExecutionStore(tmp_path / "task_requests")

    request = store.enqueue_hook_send(session_key="slack::channel::C123", prompt="hello")
    pending = store.list_pending()
    claimed = store.claim(request.id)

    assert [item.id for item in pending] == [request.id]
    assert claimed is not None
    assert claimed.request_type == "hook_send"

    store.complete(claimed, ok=True, session_key="slack::channel::C123")
    completed_path = store.completed_dir / f"{request.id}.json"
    payload = json.loads(completed_path.read_text(encoding="utf-8"))

    assert payload["ok"] is True
    assert payload["session_key"] == "slack::channel::C123"
    assert not (store.processing_dir / f"{request.id}.json").exists()


def test_request_store_constructor_does_not_requeue_processing_files(tmp_path: Path) -> None:
    root = tmp_path / "task_requests"
    store = TaskExecutionStore(root)
    request = store.enqueue_hook_send(session_key="slack::channel::C123", prompt="hello")
    claimed = store.claim(request.id)

    assert claimed is not None
    assert (store.processing_dir / f"{request.id}.json").exists()

    producer_view = TaskExecutionStore(root)

    assert not (producer_view.pending_dir / f"{request.id}.json").exists()
    assert (producer_view.processing_dir / f"{request.id}.json").exists()


def test_request_store_lists_pending_in_created_order(tmp_path: Path) -> None:
    store = TaskExecutionStore(tmp_path / "task_requests")
    first = TaskExecutionRequest(
        id="zzzz",
        request_type="hook_send",
        created_at="2026-03-31T01:00:00+00:00",
        session_key="slack::channel::C123",
        prompt="first",
    )
    second = TaskExecutionRequest(
        id="aaaa",
        request_type="hook_send",
        created_at="2026-03-31T02:00:00+00:00",
        session_key="slack::channel::C123",
        prompt="second",
    )
    store.enqueue(second)
    store.enqueue(first)

    pending = store.list_pending()

    assert [item.id for item in pending] == ["zzzz", "aaaa"]


def test_recover_processing_drops_completed_requests(tmp_path: Path) -> None:
    root = tmp_path / "task_requests"
    store = TaskExecutionStore(root)
    request = store.enqueue_hook_send(session_key="slack::channel::C123", prompt="hello")
    claimed = store.claim(request.id)

    assert claimed is not None
    store.complete(claimed, ok=True, session_key="slack::channel::C123")
    stale_processing = store.processing_dir / f"{request.id}.json"
    stale_processing.write_text(json.dumps(claimed.to_dict(), indent=2), encoding="utf-8")

    store.recover_processing()

    assert (store.completed_dir / f"{request.id}.json").exists()
    assert not stale_processing.exists()
    assert not (store.pending_dir / f"{request.id}.json").exists()


def test_drain_requests_requeues_cancelled_task_run(tmp_path: Path) -> None:
    path = tmp_path / "scheduled_tasks.json"
    request_store = TaskExecutionStore(tmp_path / "task_requests")
    store = ScheduledTaskStore(path)
    task = store.add_task(
        session_key="slack::channel::C123",
        prompt="send digest",
        schedule_type="at",
        run_at="2026-03-31T09:00:00+08:00",
        timezone_name="Asia/Shanghai",
    )
    request = request_store.enqueue_task_run(task.id)
    settings_manager = SimpleNamespace(get_store=lambda: SimpleNamespace(get_user=lambda *_args, **_kwargs: None))

    async def _handle_scheduled_message(context, message, parsed_session_key=None):
        raise asyncio.CancelledError()

    controller = SimpleNamespace(
        platform_settings_managers={"slack": settings_manager},
        message_handler=SimpleNamespace(handle_scheduled_message=_handle_scheduled_message),
    )
    service = ScheduledTaskService(controller=controller, store=store, request_store=request_store)

    try:
        asyncio.run(service._drain_requests())
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("expected CancelledError")

    reloaded = ScheduledTaskStore(path)
    updated = reloaded.get_task(task.id)
    assert updated is not None
    assert updated.last_run_at is None
    assert updated.enabled is True
    assert (request_store.pending_dir / f"{request.id}.json").exists()
    assert not (request_store.processing_dir / f"{request.id}.json").exists()
    assert not (request_store.completed_dir / f"{request.id}.json").exists()


def test_drain_requests_executes_hook_send(tmp_path: Path) -> None:
    request_store = TaskExecutionStore(tmp_path / "task_requests")
    request = request_store.enqueue_hook_send(
        session_key="slack::channel::C123::thread::171717.123",
        post_to="channel",
        prompt="ship it",
    )
    settings_manager = SimpleNamespace(get_store=lambda: SimpleNamespace(get_user=lambda *_args, **_kwargs: None))
    calls = []

    async def _handle_scheduled_message(context, message, parsed_session_key=None):
        calls.append((context, message, parsed_session_key))
        return None

    controller = SimpleNamespace(
        platform_settings_managers={"slack": settings_manager},
        message_handler=SimpleNamespace(handle_scheduled_message=_handle_scheduled_message),
    )
    service = ScheduledTaskService(
        controller=controller,
        store=ScheduledTaskStore(tmp_path / "scheduled_tasks.json"),
        request_store=request_store,
    )

    asyncio.run(service._drain_requests())

    assert len(calls) == 1
    context, message, parsed = calls[0]
    assert message == "ship it"
    assert parsed.to_key() == "slack::channel::C123::thread::171717.123"
    assert context.message_id == f"hook:{request.id}"
    assert context.thread_id == "171717.123"
    assert context.platform_specific["delivery_override"]["thread_id"] is None
    payload = json.loads((request_store.completed_dir / f"{request.id}.json").read_text(encoding="utf-8"))
    assert payload["ok"] is True


def test_run_task_request_does_not_disable_one_shot(tmp_path: Path) -> None:
    path = tmp_path / "scheduled_tasks.json"
    request_store = TaskExecutionStore(tmp_path / "task_requests")
    store = ScheduledTaskStore(path)
    task = store.add_task(
        session_key="slack::channel::C123",
        prompt="send digest",
        schedule_type="at",
        run_at="2026-03-31T09:00:00+08:00",
        timezone_name="Asia/Shanghai",
    )
    request_store.enqueue_task_run(task.id)
    settings_manager = SimpleNamespace(get_store=lambda: SimpleNamespace(get_user=lambda *_args, **_kwargs: None))

    async def _handle_scheduled_message(context, message, parsed_session_key=None):
        return None

    controller = SimpleNamespace(
        platform_settings_managers={"slack": settings_manager},
        message_handler=SimpleNamespace(handle_scheduled_message=_handle_scheduled_message),
    )
    service = ScheduledTaskService(controller=controller, store=store, request_store=request_store)

    asyncio.run(service._drain_requests())

    reloaded = ScheduledTaskStore(path)
    updated = reloaded.get_task(task.id)
    assert updated is not None
    assert updated.enabled is True
    assert updated.last_run_at is not None


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
