from __future__ import annotations

import asyncio
import threading
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.controller import Controller


def test_dispatch_to_controller_loop_runs_callback_on_controller_loop():
    controller = Controller.__new__(Controller)
    loop = asyncio.new_event_loop()
    controller._loop = loop
    result: dict[str, object] = {}

    async def callback(value: str) -> str:
        result["thread"] = threading.current_thread().name
        result["loop"] = asyncio.get_running_loop()
        result["value"] = value
        return value.upper()

    wrapped = Controller._dispatch_to_controller_loop(controller, callback)

    def _loop_runner() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    loop_thread = threading.Thread(target=_loop_runner, name="controller-loop", daemon=True)
    loop_thread.start()

    async def _invoke() -> str:
        return await wrapped("hello")

    try:
        output = asyncio.run(_invoke())
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()

    assert output == "HELLO"
    assert result["thread"] == "controller-loop"
    assert result["value"] == "hello"


def test_cleanup_sync_stops_watch_service_on_stopped_loop() -> None:
    controller = Controller.__new__(Controller)
    loop = asyncio.new_event_loop()
    controller._loop = loop
    stopped: dict[str, bool] = {"watch": False, "tasks": False}

    class _Stopper:
        def __init__(self, key: str) -> None:
            self.key = key

        async def stop(self) -> None:
            stopped[self.key] = True

    controller.scheduled_task_service = _Stopper("tasks")
    controller.watch_service = _Stopper("watch")
    controller.update_checker = type("UpdateChecker", (), {"stop": lambda self: None})()
    controller.receiver_tasks = {}
    controller.im_client = None
    controller._im_thread = None

    try:
        controller.cleanup_sync()
    finally:
        loop.close()

    assert stopped["tasks"] is True
    assert stopped["watch"] is True
