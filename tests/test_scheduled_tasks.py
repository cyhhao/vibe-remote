from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import paths
from core.scheduled_tasks import (
    ScheduledTaskService,
    ScheduledTaskStore,
    TaskExecutionRequest,
    TaskExecutionStore,
    build_session_key_for_context,
    parse_session_key,
    resolve_session_id_target,
    session_anchor_for_target,
)
from modules.im import MessageContext
from storage.db import create_sqlite_engine
from storage.background import SQLiteBackgroundTaskStore
from storage.pagination import PageRequest


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


def test_session_anchor_for_target_uses_scope_until_thread_is_explicit() -> None:
    channel = parse_session_key("slack::channel::C123")
    thread = parse_session_key("slack::channel::C123::thread::171717.123")

    assert session_anchor_for_target(channel) == "slack_C123"
    assert session_anchor_for_target(thread) == "slack_171717.123"


def test_resolve_session_id_target_keeps_scope_anchor_threadless(tmp_path: Path) -> None:
    from storage.sessions_service import SQLiteSessionsService

    db_path = tmp_path / "vibe.sqlite"
    target = parse_session_key("slack::channel::C123")
    service = SQLiteSessionsService(db_path)
    try:
        session_id = service.reserve_agent_session(
            scope_key=target.session_scope,
            agent_backend="codex",
            session_anchor=session_anchor_for_target(target),
        )
    finally:
        service.close()

    assert session_id is not None
    resolved = resolve_session_id_target(session_id, db_path=db_path)

    assert resolved.session_key.to_key() == "slack::channel::C123"
    assert resolved.session_key.thread_id is None


def test_resolve_session_id_target_preserves_reserved_user_scope(tmp_path: Path) -> None:
    from storage.sessions_service import SQLiteSessionsService

    db_path = tmp_path / "vibe.sqlite"
    target = parse_session_key("discord::user::123456789")
    service = SQLiteSessionsService(db_path)
    try:
        session_id = service.reserve_agent_session(
            scope_key=target.session_scope,
            agent_backend="codex",
            session_anchor=session_anchor_for_target(target),
        )
    finally:
        service.close()

    assert session_id is not None
    resolved = resolve_session_id_target(session_id, db_path=db_path)

    assert resolved.session_key.to_key() == "discord::user::123456789"
    assert resolved.session_key.is_dm is True


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


def test_scheduled_task_store_uses_sqlite_when_path_is_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    store = ScheduledTaskStore()
    task = store.add_task(
        name="Hourly summary",
        session_key="slack::channel::C123",
        session_id="sesk8m4q2p7x",
        prompt="hello",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="UTC",
    )

    reloaded = ScheduledTaskStore()
    saved = reloaded.get_task(task.id)
    sqlite = SQLiteBackgroundTaskStore(tmp_path / "state" / "vibe.sqlite")

    assert not (tmp_path / "state" / "scheduled_tasks.json").exists()
    assert saved is not None
    assert saved.session_id == "sesk8m4q2p7x"
    assert sqlite.get_scheduled_task(task.id)["prompt"] == "hello"


def test_sqlite_update_task_persists_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    store = ScheduledTaskStore()
    task = store.add_task(
        name="Hourly summary",
        session_key="slack::channel::C123",
        session_id="sesk8m4q2p7x",
        prompt="hello",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="UTC",
    )

    store.update_task(
        task.id,
        name="Morning summary",
        session_key="slack::channel::C456",
        session_id=None,
        prompt="updated",
        schedule_type="cron",
        post_to=None,
        deliver_key=None,
        cron="*/30 * * * *",
        run_at=None,
        timezone_name="Asia/Shanghai",
    )
    reloaded = ScheduledTaskStore()
    saved = reloaded.get_task(task.id)

    assert saved is not None
    assert saved.name == "Morning summary"
    assert saved.session_id is None
    assert saved.session_key == "slack::channel::C456"
    assert saved.prompt == "updated"
    assert saved.cron == "*/30 * * * *"


def test_task_execution_store_uses_sqlite_runs_when_root_is_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    store = TaskExecutionStore()
    request = store.enqueue_hook_send(
        session_key="slack::channel::C123",
        session_id="sesk8m4q2p7x",
        prompt="hello",
    )

    claimed = store.claim(request.id)
    assert claimed is not None
    store.complete(claimed, ok=True, session_key="slack::channel::C123", session_id="sesk8m4q2p7x")

    sqlite = SQLiteBackgroundTaskStore(tmp_path / "state" / "vibe.sqlite")
    saved = sqlite.get_run(request.id)
    assert not (tmp_path / "state" / "task_requests").exists()
    assert saved["status"] == "succeeded"
    assert saved["session_id"] == "sesk8m4q2p7x"
    assert saved["session_key"] == "slack::channel::C123"


def test_sqlite_complete_persists_resolved_run_target(tmp_path: Path) -> None:
    sqlite = SQLiteBackgroundTaskStore(tmp_path / "state" / "vibe.sqlite")
    store = TaskExecutionStore(tmp_path / "task_requests")
    store._sqlite = sqlite
    request = store.enqueue_hook_send(
        session_key="slack::channel::C123",
        session_id=None,
        prompt="hello",
    )

    claimed = store.claim(request.id)
    assert claimed is not None
    store.complete(
        claimed,
        ok=True,
        task_id="task-1",
        session_key="slack::channel::C456",
        session_id="sesk8m4q2p7x",
    )

    saved = sqlite.get_run(request.id)
    assert saved is not None
    assert saved["status"] == "succeeded"
    assert saved["task_id"] == "task-1"
    assert saved["session_key"] == "slack::channel::C456"
    assert saved["session_id"] == "sesk8m4q2p7x"


def test_sqlite_claim_only_claims_pending_runs_once(tmp_path: Path) -> None:
    sqlite = SQLiteBackgroundTaskStore(tmp_path / "state" / "vibe.sqlite")
    first_store = TaskExecutionStore(tmp_path / "task_requests")
    second_store = TaskExecutionStore(tmp_path / "task_requests-other")
    first_store._sqlite = sqlite
    second_store._sqlite = sqlite
    request = first_store.enqueue_hook_send(
        session_key="slack::channel::C123",
        prompt="hello",
    )

    first_claim = first_store.claim(request.id)
    second_claim = second_store.claim(request.id)

    assert first_claim is not None
    assert first_claim.request_type == "hook_send"
    assert second_claim is None
    assert sqlite.get_run(request.id)["status"] == "running"


def test_sqlite_cancel_pending_run_marks_canceled(tmp_path: Path) -> None:
    sqlite = SQLiteBackgroundTaskStore(tmp_path / "state" / "vibe.sqlite")
    store = TaskExecutionStore(tmp_path / "task_requests")
    store._sqlite = sqlite
    request = store.enqueue_agent_run(
        session_key="slack::channel::C123",
        message="hello",
        agent_name="default",
    )

    assert store.cancel_run(request.id) is True

    saved = sqlite.get_run(request.id)
    assert saved["status"] == "canceled"
    assert saved["cancel_requested"] is True
    assert store.claim(request.id) is None


def test_file_backend_cancel_pending_run_marks_canceled(tmp_path: Path) -> None:
    store = TaskExecutionStore(tmp_path / "task_requests")
    request = store.enqueue_agent_run(
        session_key="slack::channel::C123",
        message="hello",
        agent_name="default",
    )

    assert store.cancel_run(request.id) is True

    saved = store.get_run(request.id)
    assert saved is not None
    assert saved["status"] == "canceled"
    assert saved["cancel_requested"] is True
    assert [item["id"] for item in store.list_runs(status="canceled")] == [request.id]
    assert not (store.pending_dir / f"{request.id}.json").exists()
    assert (store.completed_dir / f"{request.id}.json").exists()
    assert store.claim(request.id) is None


def test_file_backend_cancel_running_run_sets_cancel_requested(tmp_path: Path) -> None:
    store = TaskExecutionStore(tmp_path / "task_requests")
    request = store.enqueue_agent_run(
        session_key="slack::channel::C123",
        message="hello",
        agent_name="default",
    )
    claimed = store.claim(request.id)
    assert claimed is not None

    assert store.cancel_run(request.id) is True

    saved = store.get_run(request.id)
    assert saved is not None
    assert saved["status"] == "running"
    assert saved["cancel_requested"] is True
    assert (store.processing_dir / f"{request.id}.json").exists()


def test_store_round_trip_persists_task(tmp_path: Path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    task = store.add_task(
        name="Digest",
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
    assert reloaded.get_task(task.id).name == "Digest"
    assert reloaded.get_task(task.id).session_key == "discord::channel::123"
    assert reloaded.get_task(task.id).post_to == "channel"
    assert reloaded.get_task(task.id).deliver_key == "discord::channel::456"


def test_update_task_preserves_id_and_overwrites_selected_fields(tmp_path: Path) -> None:
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    task = store.add_task(
        session_key="slack::channel::C123",
        prompt="hello",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )

    updated = store.update_task(
        task.id,
        name="Morning summary",
        session_key="slack::channel::C123::thread::171717.123",
        prompt="updated",
        schedule_type="at",
        post_to="channel",
        deliver_key=None,
        cron=None,
        run_at="2026-03-31T09:00:00+08:00",
        timezone_name="UTC",
    )

    assert updated.id == task.id
    assert updated.name == "Morning summary"
    assert updated.session_key == "slack::channel::C123::thread::171717.123"
    assert updated.prompt == "updated"
    assert updated.schedule_type == "at"
    assert updated.post_to == "channel"
    assert updated.cron is None
    assert updated.run_at == "2026-03-31T09:00:00+08:00"
    assert updated.timezone == "UTC"


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


def test_sqlite_remove_task_soft_deletes_task_but_keeps_runs(tmp_path: Path) -> None:
    sqlite = SQLiteBackgroundTaskStore(tmp_path / "state" / "vibe.sqlite")
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    store._sqlite = sqlite
    task = store.add_task(
        session_key="slack::channel::C123",
        session_id="sesk8m4q2p7x",
        prompt="send digest",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )
    sqlite.enqueue_run(
        {
            "id": "run-1",
            "request_type": "scheduled",
            "status": "succeeded",
            "task_id": task.id,
            "session_id": "sesk8m4q2p7x",
            "created_at": "2026-05-15T00:00:00+00:00",
            "updated_at": "2026-05-15T00:00:00+00:00",
            "completed_at": "2026-05-15T00:01:00+00:00",
        }
    )

    assert store.remove_task(task.id) is True

    reloaded = ScheduledTaskStore(tmp_path / "scheduled_tasks-reloaded.json")
    reloaded._sqlite = sqlite
    reloaded.load()

    assert reloaded.get_task(task.id) is None
    assert sqlite.get_scheduled_task(task.id) is None
    assert sqlite.get_run("run-1")["task_id"] == task.id


def test_store_reload_uses_size_when_mtime_does_not_change(tmp_path: Path) -> None:
    path = tmp_path / "scheduled_tasks.json"
    writer = ScheduledTaskStore(path)
    task = writer.add_task(
        session_key="slack::channel::C123",
        prompt="send digest",
        schedule_type="cron",
        cron="0 * * * *",
        timezone_name="Asia/Shanghai",
    )
    before = path.stat()

    remover = ScheduledTaskStore(path)
    assert remover.remove_task(task.id) is True

    after = path.stat()
    writer._signature = (after.st_mtime_ns, before.st_size, after.st_ino)

    assert writer.maybe_reload() is True
    assert writer.get_task(task.id) is None


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
    assert context.platform_specific["delivery_scope_session_key"] == "slack::channel::C123"
    assert context.platform_specific["scheduled_delivery_alias"]["mode"] == "sent_message"
    assert context.platform_specific["scheduled_delivery_alias"]["clear_source"] is False


def test_build_context_clears_provisional_anchor_for_cross_scope_delivery() -> None:
    settings_manager = SimpleNamespace(get_store=lambda: SimpleNamespace(get_user=lambda *_args, **_kwargs: None))
    controller = SimpleNamespace(
        platform_settings_managers={"slack": settings_manager},
        get_im_client_for_context=lambda _context: SimpleNamespace(
            should_use_thread_for_reply=lambda: True,
            should_use_thread_for_dm_session=lambda: False,
        ),
    )
    service = ScheduledTaskService(controller=controller, store=ScheduledTaskStore(Path("/tmp/nonexistent-scheduled.json")))
    session_target = parse_session_key("slack::channel::C123")
    delivery_target = parse_session_key("slack::channel::C999")

    context = asyncio.run(
        service._build_context(
            session_target,
            delivery_target=delivery_target,
            execution_id="exec-1",
            task_id="task-1",
        )
    )

    assert context.thread_id is None
    assert context.platform_specific["delivery_override"]["channel_id"] == "C999"
    assert context.platform_specific["scheduled_delivery_alias"]["mode"] == "sent_message"
    assert context.platform_specific["scheduled_delivery_alias"]["clear_source"] is True


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


def test_request_store_file_backend_filters_public_run_statuses(tmp_path: Path) -> None:
    store = TaskExecutionStore(tmp_path / "task_requests")
    queued = store.enqueue_hook_send(session_key="slack::channel::C123", prompt="queued")
    running = store.enqueue_hook_send(session_key="slack::channel::C123", prompt="running")
    failed = store.enqueue_hook_send(session_key="slack::channel::C123", prompt="failed")
    succeeded = store.enqueue_hook_send(session_key="slack::channel::C123", prompt="succeeded")

    claimed_running = store.claim(running.id)
    claimed_failed = store.claim(failed.id)
    claimed_succeeded = store.claim(succeeded.id)
    assert claimed_running is not None
    assert claimed_failed is not None
    assert claimed_succeeded is not None
    store.complete(claimed_failed, ok=False, error="boom")
    store.complete(claimed_succeeded, ok=True)

    assert [item["id"] for item in store.list_runs(status="queued")] == [queued.id]
    assert [item["id"] for item in store.list_runs(status="running")] == [running.id]
    assert [item["id"] for item in store.list_runs(status="failed")] == [failed.id]
    assert [item["id"] for item in store.list_runs(status="succeeded")] == [succeeded.id]
    assert [item["id"] for item in store.list_runs(status="pending")] == [queued.id]
    assert [item["id"] for item in store.list_runs(status="processing")] == [running.id]
    assert [item["id"] for item in store.list_runs(status="completed")] == [succeeded.id]


def test_sqlite_run_listing_pages_and_filters(tmp_path: Path) -> None:
    sqlite = SQLiteBackgroundTaskStore(tmp_path / "state" / "vibe.sqlite")
    try:
        for index in range(25):
            sqlite.enqueue_run(
                {
                    "id": f"run-{index:02d}",
                    "request_type": "agent_run" if index % 2 == 0 else "hook_send",
                    "status": "succeeded",
                    "agent_name": "helper" if index % 2 == 0 else "ops",
                    "agent_backend": "codex",
                    "session_id": "ses-alpha" if index < 20 else "ses-beta",
                    "message": f"message {index}",
                    "created_at": f"2026-05-25T00:{index:02d}:00+00:00",
                    "updated_at": f"2026-05-25T00:{index:02d}:00+00:00",
                }
            )

        first_page = sqlite.list_runs_page(page_request=PageRequest(page=1, limit=20))
        second_page = sqlite.list_runs_page(page_request=PageRequest(page=2, limit=20))
        filtered = sqlite.list_runs_page(
            agent_name="helper",
            session_id="ses-beta",
            created_after="2026-05-25T00:20:00+00:00",
            query="message 24",
            page_request=PageRequest(page=1, limit=20),
        )

        assert first_page.has_more is True
        assert [item["id"] for item in first_page.items[:2]] == ["run-24", "run-23"]
        assert second_page.has_more is False
        assert [item["id"] for item in second_page.items] == ["run-04", "run-03", "run-02", "run-01", "run-00"]
        assert [item["id"] for item in filtered.items] == ["run-24"]
    finally:
        sqlite.close()


def test_sqlite_run_query_filter_treats_like_wildcards_as_literals(tmp_path: Path) -> None:
    sqlite = SQLiteBackgroundTaskStore(tmp_path / "state" / "vibe.sqlite")
    try:
        for run_id, message in [
            ("run-underscore", "foo_bar"),
            ("run-letter", "fooxbar"),
            ("run-percent", "100% done"),
            ("run-plain", "1000 done"),
        ]:
            sqlite.enqueue_run(
                {
                    "id": run_id,
                    "request_type": "agent_run",
                    "status": "succeeded",
                    "message": message,
                    "created_at": "2026-05-25T00:00:00+00:00",
                    "updated_at": "2026-05-25T00:00:00+00:00",
                }
            )

        underscore = sqlite.list_runs_page(query="foo_", page_request=PageRequest(page=1, limit=20))
        percent = sqlite.list_runs_page(query="100%", page_request=PageRequest(page=1, limit=20))

        assert [item["id"] for item in underscore.items] == ["run-underscore"]
        assert [item["id"] for item in percent.items] == ["run-percent"]
    finally:
        sqlite.close()


def test_runtime_session_reservation_uses_legacy_scope_backend(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "state" / "vibe.sqlite"
    monkeypatch.setattr(paths, "get_state_dir", lambda: db_path.parent)
    monkeypatch.setattr(paths, "get_sqlite_state_path", lambda: db_path)

    from storage.importer import ensure_sqlite_state
    from storage.models import scope_settings
    from storage.settings_service import upsert_scope

    ensure_sqlite_state(db_path=db_path, primary_platform="slack")
    with create_sqlite_engine(db_path).begin() as conn:
        now = "2026-05-22T00:00:00+00:00"
        scope_id = upsert_scope(conn, "slack", "channel", "C123", now=now)
        conn.execute(
            scope_settings.insert().values(
                scope_id=scope_id,
                enabled=1,
                role=None,
                workdir=None,
                agent_name=None,
                agent_backend="codex",
                agent_variant=None,
                model=None,
                reasoning_effort=None,
                require_mention=None,
                settings_version=1,
                settings_json=json.dumps({"routing": {"agent_backend": "codex"}}),
                created_at=now,
                updated_at=now,
            )
        )

    controller = SimpleNamespace(agent_router=SimpleNamespace(global_default="claude"))
    service = ScheduledTaskService(
        controller=controller,
        store=ScheduledTaskStore(tmp_path / "scheduled_tasks.json"),
        request_store=TaskExecutionStore(tmp_path / "task_requests"),
    )

    session_id = service._reserve_runtime_session(agent_name=None, deliver_key="slack::channel::C123")
    target = resolve_session_id_target(session_id, db_path=db_path)

    assert target.agent_backend == "codex"
    assert target.agent_name is None


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

    async def _exercise() -> None:
        # The drain now dispatches concurrently and returns immediately, so
        # the CancelledError surfaces on the spawned execution task rather
        # than out of _drain_requests itself. Awaiting it lets the requeue
        # path (in _execute_claimed_request) run.
        await service._drain_requests()
        execution = service._inflight_executions.get(request.id)
        assert execution is not None
        try:
            await execution
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("expected CancelledError on the execution task")

    asyncio.run(_exercise())

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


def test_drain_requests_reserves_watch_create_per_run_before_session_validation(tmp_path: Path) -> None:
    request_store = TaskExecutionStore(tmp_path / "task_requests")
    request = request_store.enqueue_definition_run(
        definition_id="watch-1",
        run_type="watch",
        source_kind="watch",
        session_key="",
        session_id=None,
        post_to=None,
        deliver_key="slack::channel::C123",
        prompt="summarize waiter output",
        agent_name="release-reviewer",
        session_policy="create_per_run",
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
    service._reserve_runtime_session = lambda **_kwargs: "ses-created"  # type: ignore[method-assign]

    async def _execute_request(**kwargs):
        calls.append(kwargs)
        return None

    service._execute_request = _execute_request  # type: ignore[method-assign]

    asyncio.run(service._drain_requests())

    assert calls == [
        {
            "session_key": "",
            "session_id": "ses-created",
            "post_to": None,
            "deliver_key": "slack::channel::C123",
            "prompt": "summarize waiter output",
            "execution_id": request.id,
            "task_id": "watch-1",
            "trigger_kind": "watch",
            "agent_name": "release-reviewer",
        }
    ]
    payload = json.loads((request_store.completed_dir / f"{request.id}.json").read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["session_id"] == "ses-created"
    assert payload["session_key"] == ""


def test_drain_requests_records_scheduled_create_per_run_reserved_session(tmp_path: Path) -> None:
    request_store = TaskExecutionStore(tmp_path / "task_requests")
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    task = store.add_task(
        session_key="",
        session_id=None,
        prompt="daily review",
        schedule_type="cron",
        cron="0 9 * * *",
        timezone_name="UTC",
        deliver_key="slack::channel::C123",
        agent_name="release-reviewer",
        session_policy="create_per_run",
    )
    request = request_store.enqueue_task_run(task.id, source_kind="scheduler", task=task)
    settings_manager = SimpleNamespace(get_store=lambda: SimpleNamespace(get_user=lambda *_args, **_kwargs: None))
    calls = []

    async def _handle_scheduled_message(context, message, parsed_session_key=None):
        calls.append((context, message, parsed_session_key))
        return None

    controller = SimpleNamespace(
        platform_settings_managers={"slack": settings_manager},
        message_handler=SimpleNamespace(handle_scheduled_message=_handle_scheduled_message),
    )
    service = ScheduledTaskService(controller=controller, store=store, request_store=request_store)
    service._reserve_runtime_session = lambda **_kwargs: "ses-created"  # type: ignore[method-assign]

    async def _execute_request(**kwargs):
        calls.append(kwargs)
        return None

    service._execute_request = _execute_request  # type: ignore[method-assign]

    asyncio.run(service._drain_requests())

    assert calls == [
        {
            "session_key": "",
            "session_id": "ses-created",
            "post_to": None,
            "deliver_key": "slack::channel::C123",
            "prompt": "daily review",
            "execution_id": request.id,
            "task_id": task.id,
            "trigger_kind": "scheduled",
            "agent_name": "release-reviewer",
        }
    ]
    payload = json.loads((request_store.completed_dir / f"{request.id}.json").read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["session_id"] == "ses-created"
    assert payload["session_key"] == ""


def test_drain_requests_agent_run_passes_agent_name(tmp_path: Path) -> None:
    request_store = TaskExecutionStore(tmp_path / "task_requests")
    request = request_store.enqueue_agent_run(
        session_key="slack::channel::C123",
        message="review build",
        agent_name="release-reviewer",
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
    assert message == "review build"
    assert parsed.to_key() == "slack::channel::C123"
    assert context.message_id == f"agent_run:{request.id}"
    assert context.platform_specific["vibe_agent_name"] == "release-reviewer"
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
        await service.stop()

    asyncio.run(_exercise())


def test_watch_store_respawns_after_unexpected_cancellation(tmp_path: Path) -> None:
    """A spurious CancelledError must not silently kill the drain loop."""
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    controller = SimpleNamespace(platform_settings_managers={})
    service = ScheduledTaskService(controller=controller, store=store)
    service.scheduler = _StubScheduler()

    started = asyncio.Event()

    async def _watch_store():
        started.set()
        await asyncio.Event().wait()

    service._watch_store = _watch_store  # type: ignore[method-assign]

    async def _exercise():
        service.start()
        first_task = service._reconcile_task
        assert first_task is not None
        await asyncio.wait_for(started.wait(), timeout=1)

        started.clear()
        first_task.cancel()
        for _ in range(50):
            await asyncio.sleep(0)
            if service._reconcile_task is not None and service._reconcile_task is not first_task:
                break
        assert service._reconcile_task is not None
        assert service._reconcile_task is not first_task
        assert service._watch_store_restart_count == 1

        await asyncio.wait_for(started.wait(), timeout=1)
        await service.stop()

    asyncio.run(_exercise())


def test_watch_store_respawns_after_unexpected_exception(tmp_path: Path) -> None:
    """If the watch coroutine crashes with a non-Cancelled exception it must respawn."""
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    controller = SimpleNamespace(platform_settings_managers={})
    service = ScheduledTaskService(controller=controller, store=store)
    service.scheduler = _StubScheduler()

    invocations: list[int] = []

    async def _watch_store():
        invocations.append(1)
        if len(invocations) == 1:
            raise RuntimeError("boom")
        await asyncio.Event().wait()

    service._watch_store = _watch_store  # type: ignore[method-assign]

    async def _exercise():
        service.start()
        for _ in range(50):
            await asyncio.sleep(0)
            if len(invocations) >= 2:
                break
        assert len(invocations) >= 2
        assert service._watch_store_restart_count == 1
        await service.stop()

    asyncio.run(_exercise())


def test_watch_store_does_not_respawn_after_stop(tmp_path: Path) -> None:
    """stop() cancels the task and must not trigger a respawn."""
    store = ScheduledTaskStore(tmp_path / "scheduled_tasks.json")
    controller = SimpleNamespace(platform_settings_managers={})
    service = ScheduledTaskService(controller=controller, store=store)
    service.scheduler = _StubScheduler()

    async def _watch_store():
        await asyncio.Event().wait()

    service._watch_store = _watch_store  # type: ignore[method-assign]

    async def _exercise():
        service.start()
        first_task = service._reconcile_task
        assert first_task is not None
        await service.stop()
        assert service._reconcile_task is None
        assert service._watch_store_restart_count == 0
        assert first_task.cancelled() or first_task.done()

    asyncio.run(_exercise())


def test_drain_does_not_block_on_hung_execution(tmp_path: Path) -> None:
    """A turn that never returns must not stall delivery of other sessions.

    Regression for watch follow-up runs piling up in ``queued`` after one
    execution hung: the drain loop used to await each execution inline.
    """

    async def _exercise() -> None:
        store = TaskExecutionStore(tmp_path / "reqs")
        hung = store.enqueue_hook_send(session_key="slack::channel::A", prompt="hangs")
        fast = store.enqueue_hook_send(session_key="slack::channel::B", prompt="fast")

        controller = SimpleNamespace(platform_settings_managers={})
        service = ScheduledTaskService(
            controller=controller,
            store=ScheduledTaskStore(tmp_path / "scheduled_tasks.json"),
            request_store=store,
        )

        started: list[str] = []
        never = asyncio.Event()

        async def fake_execute(request):
            started.append(request.id)
            if request.id == hung.id:
                await never.wait()  # simulate an agent turn that never returns
                return
            service.request_store.complete(request, ok=True)

        service._execute_claimed_request = fake_execute  # type: ignore[assignment]

        # Should return promptly even though one execution hangs forever.
        await asyncio.wait_for(service._drain_requests(), timeout=1.0)
        # Let the fast execution finish.
        await asyncio.sleep(0.05)

        assert hung.id in started and fast.id in started
        # Fast session delivered despite the hung one still in flight.
        assert [item["id"] for item in store.list_runs(status="succeeded")] == [fast.id]
        assert hung.id in service._inflight_executions
        assert "slack::channel::A" in service._inflight_sessions
        assert "slack::channel::B" not in service._inflight_sessions

        # Cleanup: release the hung task.
        never.set()
        hung_task = service._inflight_executions.get(hung.id)
        if hung_task is not None:
            await hung_task

    asyncio.run(_exercise())


def test_drain_serializes_executions_per_session(tmp_path: Path) -> None:
    """Two requests for the same session never run concurrently; the second
    stays queued until the first finishes."""

    async def _exercise() -> None:
        store = TaskExecutionStore(tmp_path / "reqs")
        first = store.enqueue_hook_send(session_key="slack::channel::A", prompt="first")
        second = store.enqueue_hook_send(session_key="slack::channel::A", prompt="second")

        controller = SimpleNamespace(platform_settings_managers={})
        service = ScheduledTaskService(
            controller=controller,
            store=ScheduledTaskStore(tmp_path / "scheduled_tasks.json"),
            request_store=store,
        )

        started: list[str] = []
        gate = asyncio.Event()

        async def fake_execute(request):
            started.append(request.id)
            await gate.wait()
            service.request_store.complete(request, ok=True)

        service._execute_claimed_request = fake_execute  # type: ignore[assignment]

        await asyncio.wait_for(service._drain_requests(), timeout=1.0)
        await asyncio.sleep(0.05)

        # Only the first claimed; the second stays queued behind the same session.
        assert started == [first.id]
        assert [item["id"] for item in store.list_runs(status="queued")] == [second.id]

        # Release the first; a second drain now picks up the queued one.
        gate.set()
        first_task = service._inflight_executions.get(first.id)
        if first_task is not None:
            await first_task
        await asyncio.wait_for(service._drain_requests(), timeout=1.0)
        await asyncio.sleep(0.05)
        assert started == [first.id, second.id]
        second_task = service._inflight_executions.get(second.id)
        if second_task is not None:
            await second_task

    asyncio.run(_exercise())
