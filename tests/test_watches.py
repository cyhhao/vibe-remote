from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.scheduled_tasks import TaskExecutionStore
from core.watches import ManagedWatchService, ManagedWatchStore, WatchRuntimeStateStore


def test_managed_watch_store_round_trip(tmp_path: Path) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    watch = store.add_watch(
        name="Watch CI",
        session_key="slack::channel::C123",
        command=["python3", "wait.py"],
        shell_command=None,
        prefix="CI finished.",
        cwd="/tmp",
        mode="forever",
        timeout_seconds=600,
        lifetime_timeout_seconds=3600,
        retry_exit_codes=[1, 75],
        retry_delay_seconds=45,
        post_to="channel",
        deliver_key=None,
    )

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    reloaded = ManagedWatchStore(store.path)
    saved = reloaded.get_watch(watch.id)

    assert payload["watches"][0]["id"] == watch.id
    assert saved is not None
    assert saved.name == "Watch CI"
    assert saved.mode == "forever"
    assert saved.retry_exit_codes == [1, 75]
    assert saved.post_to == "channel"


def test_managed_watch_service_once_success_enqueues_hook_and_disables(tmp_path: Path) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    request_store = TaskExecutionStore(tmp_path / "task_requests")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    watch = store.add_watch(
        name="Wait once",
        session_key="slack::channel::C123",
        command=["python3", "-c", "print('waiter output')"],
        shell_command=None,
        prefix="The waiter finished.",
        cwd=None,
        mode="once",
        timeout_seconds=5,
        lifetime_timeout_seconds=0,
        retry_exit_codes=[1],
        retry_delay_seconds=30,
        post_to=None,
        deliver_key=None,
    )
    service = ManagedWatchService(
        controller=SimpleNamespace(),
        store=store,
        request_store=request_store,
        runtime_store=runtime_store,
    )

    async def _run() -> None:
        service.start()
        for _ in range(100):
            if watch.id not in service._active_tasks:
                break
            await asyncio.sleep(0.05)
        await service.stop()

    asyncio.run(_run())

    pending = request_store.list_pending()
    saved = store.get_watch(watch.id)

    assert len(pending) == 1
    assert pending[0].request_type == "hook_send"
    assert pending[0].prompt == "The waiter finished.\n\nwaiter output"
    assert saved is not None
    assert saved.enabled is False
    assert saved.last_exit_code == 0
    assert saved.last_event_at is not None


def test_managed_watch_service_forever_timeout_is_silent_per_cycle(tmp_path: Path) -> None:
    store = ManagedWatchStore(tmp_path / "watches.json")
    request_store = TaskExecutionStore(tmp_path / "task_requests")
    runtime_store = WatchRuntimeStateStore(tmp_path / "watch_runtime.json")
    watch = store.add_watch(
        name="Wait forever",
        session_key="slack::channel::C123",
        command=["python3", "-c", "import time; time.sleep(0.2)"],
        shell_command=None,
        prefix="Should stay silent.",
        cwd=None,
        mode="forever",
        timeout_seconds=0.05,
        lifetime_timeout_seconds=0,
        retry_exit_codes=[1],
        retry_delay_seconds=0.01,
        post_to=None,
        deliver_key=None,
    )
    service = ManagedWatchService(
        controller=SimpleNamespace(),
        store=store,
        request_store=request_store,
        runtime_store=runtime_store,
    )

    async def _run() -> None:
        service.start()
        await asyncio.sleep(0.2)
        await service.stop()

    asyncio.run(_run())

    saved = store.get_watch(watch.id)

    assert request_store.list_pending() == []
    assert saved is not None
    assert saved.enabled is True
    assert saved.last_exit_code == 124
