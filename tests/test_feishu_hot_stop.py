from __future__ import annotations

import asyncio

from config.v2_config import LarkConfig
from modules.im.feishu import FeishuBot


def test_feishu_stop_disconnects_nested_ws_client(monkeypatch):
    import lark_oapi.ws.client as ws_client_module

    loop = asyncio.new_event_loop()
    disconnected = []

    class _WsClient:
        async def _disconnect(self):
            disconnected.append(True)

    bot = FeishuBot(LarkConfig(app_id="cli_a", app_secret="secret"))
    bot._ws_client = _WsClient()
    bot._loop = loop
    monkeypatch.setattr(ws_client_module, "loop", loop)

    try:
        bot.stop()
    finally:
        loop.close()

    assert disconnected == [True]


def test_feishu_stop_stops_running_lark_ws_loop(monkeypatch):
    import lark_oapi.ws.client as ws_client_module

    loop = asyncio.new_event_loop()
    disconnected = []

    class _WsClient:
        async def _disconnect(self):
            disconnected.append(True)

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    import threading

    thread = threading.Thread(target=_run_loop, daemon=True)
    thread.start()
    try:
        assert thread.is_alive()
        bot = FeishuBot(LarkConfig(app_id="cli_a", app_secret="secret"))
        bot._ws_client = _WsClient()
        monkeypatch.setattr(ws_client_module, "loop", loop)

        bot.stop()
        thread.join(timeout=2)
    finally:
        if thread.is_alive():
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2)
        loop.close()

    assert disconnected == [True]
    assert thread.is_alive() is False
