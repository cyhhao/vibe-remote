from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.im.base import BaseIMClient, BaseIMConfig, MessageContext
from modules.im.multi import MultiIMClient
from modules.settings_manager import MultiSettingsManager
from config.v2_sessions import ActivePollInfo


@dataclass
class _StubConfig(BaseIMConfig):
    def validate(self) -> None:
        return None


class _StubClient(BaseIMClient):
    def __init__(self, name: str):
        super().__init__(_StubConfig())
        self.name = name
        self.sent = []

    async def send_message(self, context, text, parse_mode=None, reply_to=None):
        self.sent.append((context.platform, context.channel_id, text))
        return self.name

    async def send_message_with_buttons(self, context, text, keyboard, parse_mode=None):
        return self.name

    async def edit_message(self, context, message_id, text=None, keyboard=None, parse_mode=None):
        return True

    async def answer_callback(self, callback_id, text=None, show_alert=False):
        return True

    def register_handlers(self):
        return None

    def run(self):
        return None

    async def get_user_info(self, user_id: str):
        return {"id": user_id, "name": self.name}

    async def get_channel_info(self, channel_id: str):
        return {"id": channel_id, "name": self.name}

    async def send_dm(self, user_id: str, text: str, **kwargs):
        self.sent.append(("dm", user_id, text))
        return self.name

    def format_markdown(self, text: str) -> str:
        return text


def test_multi_settings_manager_routes_scoped_keys(tmp_path):
    manager = MultiSettingsManager(
        ["slack", "wechat"], settings_file=str(tmp_path / "settings.json"), primary_platform="slack"
    )

    manager.set_custom_cwd("wechat::user-1", "/tmp/wx")
    manager.set_custom_cwd("slack::C123", "/tmp/slack")

    assert manager.get_custom_cwd("wechat::user-1") == "/tmp/wx"
    assert manager.get_custom_cwd("slack::C123") == "/tmp/slack"
    assert manager.managers["slack"].sessions is manager.sessions
    assert manager.managers["wechat"].sessions is manager.sessions


def test_multi_im_client_routes_send_by_context_platform():
    slack = _StubClient("slack")
    wechat = _StubClient("wechat")
    client = MultiIMClient({"slack": slack, "wechat": wechat}, primary_platform="slack")

    asyncio.run(client.send_message(MessageContext(user_id="u", channel_id="c", platform="wechat"), "hello"))

    assert slack.sent == []
    assert wechat.sent == [("wechat", "c", "hello")]


def test_multi_im_client_annotates_inbound_context_platform():
    slack = _StubClient("slack")
    wechat = _StubClient("wechat")
    client = MultiIMClient({"slack": slack, "wechat": wechat}, primary_platform="slack")
    captured: list[str | None] = []

    async def on_message(context: MessageContext, text: str):
        captured.append(context.platform)

    client.register_callbacks(on_message=on_message)

    callback = wechat.on_message_callback
    assert callback is not None
    asyncio.run(callback(MessageContext(user_id="u", channel_id="c"), "hello"))

    assert captured == ["wechat"]


def test_multi_im_client_routes_scoped_identity_lookups():
    slack = _StubClient("slack")
    wechat = _StubClient("wechat")
    client = MultiIMClient({"slack": slack, "wechat": wechat}, primary_platform="slack")

    user_info = asyncio.run(client.get_user_info("wechat::wx-user"))
    channel_info = asyncio.run(client.get_channel_info("wechat::wx-chat"))
    asyncio.run(client.send_dm("wechat::wx-user", "hello"))

    assert user_info == {"id": "wx-user", "name": "wechat"}
    assert channel_info == {"id": "wx-chat", "name": "wechat"}
    assert wechat.sent[-1] == ("dm", "wx-user", "hello")


def test_active_poll_info_round_trips_platform():
    poll = ActivePollInfo(
        opencode_session_id="ses-1",
        base_session_id="base-1",
        channel_id="chan-1",
        thread_id="thread-1",
        settings_key="discord::chan-1",
        working_path="/tmp/work",
        user_id="user-1",
        platform="discord",
    )

    restored = ActivePollInfo.from_dict(poll.to_dict())

    assert restored.platform == "discord"
