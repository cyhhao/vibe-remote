"""Workbench-only runtime: zero external IM platforms.

A workbench-only install enables no Slack/Discord/Telegram/Lark/WeChat
platform. The Avibe Workbench (in-process Web UI) is the sole inbound surface.
These tests cover the two delicate seams that make that work end-to-end:

1. Controller ``_init_modules`` must register the in-process ``AvibeBot`` as the
   SOLE IM client, anchor the primary platform to "avibe", and wire a settings
   manager for it — without crashing on the empty enabled-platform set.
2. ``AvibeBot.run`` must fire ``on_ready`` exactly once (so the controller starts
   poll-restore / scheduled tasks / update checker) and then BLOCK so the
   IM-runtime thread does not return, keeping the controller's ``run_forever``
   alive until ``stop`` is called.

The has-IM path is exercised by the broader multi-platform suite; here we only
assert the workbench-only behavior that previously raised.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_compat import to_app_config
from config.v2_config import V2Config
from core.controller import Controller
from modules.im.avibe import AvibeBot, AvibeConfig
from modules.im.base import MessageContext


def _workbench_only_app_config():
    payload = {
        "platform": "slack",
        "platforms": {"enabled": [], "primary": "slack"},
        "mode": "self_host",
        "version": "v2",
        "runtime": {"default_cwd": "_tmp", "log_level": "INFO"},
        "agents": {
            "default_backend": "opencode",
            "opencode": {"enabled": True, "cli_path": "opencode"},
        },
        "setup_completed": True,
    }
    return to_app_config(V2Config.from_payload(payload))


def test_init_modules_registers_avibe_as_sole_client_for_workbench_only():
    app_config = _workbench_only_app_config()
    # The compat view of a workbench-only config exposes no enabled platforms.
    assert app_config.enabled_platforms() == []

    controller = Controller.__new__(Controller)
    controller.config = app_config
    controller.enabled_platforms = list(app_config.enabled_platforms())
    controller.primary_platform = getattr(getattr(app_config, "platforms", None), "primary", app_config.platform)

    controller._init_modules()

    assert list(controller.im_clients.keys()) == ["avibe"]
    assert isinstance(controller.im_client, AvibeBot)
    assert controller.primary_platform == "avibe"
    # The primary-anchored settings manager resolves (no KeyError on an empty
    # enabled-platform set) for both the no-context and avibe-context cases.
    assert controller.get_settings_manager_for_context(None).platform == "avibe"
    avibe_ctx = MessageContext(user_id="u", channel_id="c", platform="avibe")
    assert controller.get_settings_manager_for_context(avibe_ctx).platform == "avibe"
    # Agent routing has a route for the avibe primary.
    assert "avibe" in controller.agent_router.platform_routes


def test_avibe_run_fires_on_ready_once_then_blocks_until_stop():
    # Stand-in for the controller event loop + its _dispatch_to_controller_loop
    # bridge, so we exercise the real cross-loop hand-off AvibeBot.run relies on.
    controller_loop = asyncio.new_event_loop()
    ready_calls: list[bool] = []

    def _start_loop() -> None:
        asyncio.set_event_loop(controller_loop)
        controller_loop.run_forever()

    loop_thread = threading.Thread(target=_start_loop, daemon=True)
    loop_thread.start()
    try:
        # Wait for the loop to actually be running before scheduling onto it.
        for _ in range(100):
            if controller_loop.is_running():
                break
            time.sleep(0.01)

        async def _on_ready() -> None:
            assert asyncio.get_running_loop() is controller_loop
            ready_calls.append(True)

        def _dispatch_to_controller_loop(callback):
            async def _wrapped(*args, **kwargs):
                try:
                    current = asyncio.get_running_loop()
                except RuntimeError:
                    current = None
                if current is controller_loop:
                    return await callback(*args, **kwargs)
                future = asyncio.run_coroutine_threadsafe(callback(*args, **kwargs), controller_loop)
                return await asyncio.wrap_future(future)

            return _wrapped

        bot = AvibeBot(AvibeConfig())
        bot.register_callbacks(on_message=None, on_ready=_dispatch_to_controller_loop(_on_ready))

        returned: list[bool] = []

        def _run_im() -> None:
            bot.run()
            returned.append(True)

        im_thread = threading.Thread(target=_run_im, name="im-runtime", daemon=True)
        im_thread.start()

        # on_ready fires once and run() blocks (the thread stays alive).
        for _ in range(100):
            if ready_calls:
                break
            time.sleep(0.01)
        assert ready_calls == [True]
        time.sleep(0.05)
        assert im_thread.is_alive() is True
        assert returned == []

        # stop() (called from another thread, like cleanup_sync) releases run().
        bot.stop()
        im_thread.join(timeout=3)
        assert im_thread.is_alive() is False
        assert returned == [True]
        # on_ready did not fire a second time.
        assert ready_calls == [True]
    finally:
        controller_loop.call_soon_threadsafe(controller_loop.stop)
        loop_thread.join(timeout=3)
        controller_loop.close()


def test_avibe_stop_is_safe_before_run():
    # cleanup_sync may call stop() even if run() never started (e.g. early
    # failure). It must not raise.
    bot = AvibeBot(AvibeConfig())
    bot.stop()
