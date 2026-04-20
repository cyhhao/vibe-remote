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
from modules.agents.opencode.poll_loop import OpenCodePollLoop


@dataclass
class _StubConfig(BaseIMConfig):
    def validate(self) -> None:
        return None


class _StubClient(BaseIMClient):
    def __init__(self, name: str, *, supports_editing: bool = True):
        super().__init__(_StubConfig())
        self.name = name
        self._supports_editing = supports_editing
        self.sent = []
        self.removed = []
        self.dismissed = []

    async def send_message(self, context, text, parse_mode=None, reply_to=None):
        self.sent.append((context.platform, context.channel_id, text))
        return self.name

    async def send_message_with_buttons(self, context, text, keyboard, parse_mode=None):
        return self.name

    async def edit_message(self, context, message_id, text=None, keyboard=None, parse_mode=None):
        return True

    def supports_message_editing(self, context=None):
        return self._supports_editing

    async def remove_inline_keyboard(self, context, message_id, text=None, parse_mode=None):
        self.removed.append((context.platform, message_id, text))
        return True

    async def dismiss_form_message(self, context):
        self.dismissed.append((context.platform, context.message_id))

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

    async def download_file(self, file_info, max_bytes=None, timeout_seconds=30):
        self.sent.append(("download", file_info.get("platform"), file_info.get("name")))
        return b"data"

    async def download_file_to_path(self, file_info, target_path, max_bytes=None, timeout_seconds=30):
        self.sent.append(("download_to_path", file_info.get("platform"), target_path))
        from modules.im.base import FileDownloadResult

        return FileDownloadResult(True, target_path)

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


def test_multi_im_client_routes_message_edit_capability_by_context_platform():
    slack = _StubClient("slack")
    wechat = _StubClient("wechat", supports_editing=False)
    client = MultiIMClient({"slack": slack, "wechat": wechat}, primary_platform="slack")

    assert client.supports_message_editing(MessageContext(user_id="u", channel_id="c", platform="slack"))
    assert not client.supports_message_editing(MessageContext(user_id="u", channel_id="c", platform="wechat"))


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


def test_active_poll_info_round_trips_restored_typing_context():
    poll = ActivePollInfo(
        opencode_session_id="ses-1",
        base_session_id="base-1",
        channel_id="chan-1",
        thread_id="thread-1",
        settings_key="chan-1",
        working_path="/tmp/work",
        user_id="user-1",
        platform="wechat",
        typing_indicator_active=True,
        context_token="ctx-1",
    )

    restored = ActivePollInfo.from_dict(poll.to_dict())

    assert restored.platform == "wechat"
    assert restored.typing_indicator_active is True
    assert restored.context_token == "ctx-1"


def test_opencode_restored_ack_preserves_wechat_typing_context():
    captured = []

    class _StubAgent:
        async def _remove_ack_reaction(self, request):
            captured.append(request)

    poll = ActivePollInfo(
        opencode_session_id="ses-1",
        base_session_id="base-1",
        channel_id="chan-1",
        thread_id="thread-1",
        settings_key="chan-1",
        working_path="/tmp/work",
        user_id="user-1",
        platform="wechat",
        typing_indicator_active=True,
        context_token="ctx-1",
    )
    loop = OpenCodePollLoop(_StubAgent(), question_handler=None)

    asyncio.run(loop.remove_restored_ack(poll))

    request = captured[0]
    assert request.typing_indicator_active is True
    assert request.context.platform == "wechat"
    assert request.context.platform_specific == {"platform": "wechat", "context_token": "ctx-1"}


def test_multi_im_client_routes_download_by_file_info_platform():
    slack = _StubClient("slack")
    wechat = _StubClient("wechat")
    client = MultiIMClient({"slack": slack, "wechat": wechat}, primary_platform="slack")

    asyncio.run(client.download_file_to_path({"platform": "wechat", "name": "a.jpg"}, "/tmp/a.jpg"))

    assert slack.sent == []
    assert wechat.sent == [("download_to_path", "wechat", "/tmp/a.jpg")]


def test_multi_im_client_routes_remove_inline_keyboard_by_context_platform():
    slack = _StubClient("slack")
    lark = _StubClient("lark")
    client = MultiIMClient({"slack": slack, "lark": lark}, primary_platform="slack")

    asyncio.run(
        client.remove_inline_keyboard(
            MessageContext(user_id="u", channel_id="c", platform="lark"),
            "om_123",
        )
    )

    assert slack.removed == []
    assert lark.removed == [("lark", "om_123", None)]


def test_multi_im_client_routes_dismiss_form_message_by_context_platform():
    slack = _StubClient("slack")
    lark = _StubClient("lark")
    client = MultiIMClient({"slack": slack, "lark": lark}, primary_platform="slack")

    asyncio.run(
        client.dismiss_form_message(
            MessageContext(user_id="u", channel_id="c", platform="lark", message_id="om_456")
        )
    )

    assert slack.dismissed == []
    assert lark.dismissed == [("lark", "om_456")]


def test_multi_im_client_on_ready_fires_only_after_all_platforms():
    """on_ready callback must wait for all platform clients to be ready."""
    slack = _StubClient("slack")
    wechat = _StubClient("wechat")
    client = MultiIMClient({"slack": slack, "wechat": wechat}, primary_platform="slack")

    ready_calls: list[bool] = []

    async def _on_ready():
        ready_calls.append(True)

    client.register_callbacks(on_message=None, on_ready=_on_ready)

    # Simulate only Slack becoming ready — on_ready should NOT fire yet
    slack_on_ready = slack.on_ready_callback
    assert slack_on_ready is not None
    asyncio.run(slack_on_ready())
    assert ready_calls == [], "on_ready fired before all platforms were ready"

    # Now simulate WeChat becoming ready — on_ready should fire exactly once
    wechat_on_ready = wechat.on_ready_callback
    assert wechat_on_ready is not None
    asyncio.run(wechat_on_ready())
    assert ready_calls == [True], "on_ready should fire exactly once after all platforms are ready"

    # Calling again should not fire a second time
    asyncio.run(wechat_on_ready())
    assert len(ready_calls) == 1, "on_ready should not fire more than once"
