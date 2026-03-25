from __future__ import annotations

from dataclasses import dataclass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.handlers.session_handler import SessionHandler
from modules.im import MessageContext


@dataclass
class _Config:
    platform: str = "discord"


class _Controller:
    def __init__(
        self,
        *,
        platform: str = "discord",
        dm_threads: bool = False,
        channel_message_sessions: bool = True,
    ) -> None:
        self.config = _Config()
        self.config.platform = platform
        self.im_client = type(
            "IM",
            (),
            {
                "formatter": None,
                "should_use_thread_for_dm_session": lambda self: dm_threads,
                "should_use_message_id_for_channel_session": lambda self, context=None: channel_message_sessions,
            },
        )()
        self.settings_manager = type("Settings", (), {"sessions": None})()
        self.session_manager = object()
        self.claude_sessions = {}
        self.receiver_tasks = {}
        self.stored_session_mappings = {}

    def get_cwd(self, context: MessageContext) -> str:
        return "/tmp/workdir"

    def _get_settings_key(self, context: MessageContext) -> str:
        return context.user_id if (context.platform_specific or {}).get("is_dm") else context.channel_id

    def _get_session_key(self, context: MessageContext) -> str:
        return f"{getattr(context, 'platform', None) or 'test'}::{self._get_settings_key(context)}"

    def get_im_client_for_context(self, context: MessageContext):
        return self.im_client


def test_dm_session_base_id_uses_stable_channel_id() -> None:
    handler = SessionHandler(_Controller(platform="discord", dm_threads=False))
    context = MessageContext(
        user_id="u-1",
        channel_id="dm-123",
        thread_id="thread-999",
        message_id="msg-999",
        platform_specific={"is_dm": True},
    )

    assert handler.get_base_session_id(context) == "discord_dm-123"


def test_dm_session_base_id_uses_thread_when_platform_supports_dm_threads() -> None:
    handler = SessionHandler(_Controller(platform="lark", dm_threads=True))
    context = MessageContext(
        user_id="u-1",
        channel_id="dm-123",
        thread_id="thread-999",
        message_id="msg-999",
        platform_specific={"is_dm": True},
    )

    assert handler.get_base_session_id(context) == "lark_thread-999"


def test_base_session_id_prefers_context_platform_over_primary_config() -> None:
    handler = SessionHandler(_Controller(platform="slack", dm_threads=False))
    context = MessageContext(
        user_id="u-1",
        channel_id="wx-123",
        platform="wechat",
        message_id="msg-42",
        platform_specific={"is_dm": False},
    )

    assert handler.get_base_session_id(context) == "wechat_msg-42"


def test_slack_dm_session_base_id_uses_thread_when_supported() -> None:
    handler = SessionHandler(_Controller(platform="slack", dm_threads=True))
    context = MessageContext(
        user_id="u-1",
        channel_id="D123",
        thread_id="171717.999",
        message_id="171717.111",
        platform_specific={"is_dm": True},
    )

    assert handler.get_base_session_id(context) == "slack_171717.999"


def test_channel_session_base_id_keeps_thread_or_message_behavior() -> None:
    handler = SessionHandler(_Controller())
    context = MessageContext(
        user_id="u-1",
        channel_id="chan-123",
        message_id="msg-999",
        platform_specific={"is_dm": False},
    )

    assert handler.get_base_session_id(context) == "discord_msg-999"


def test_telegram_plain_group_session_base_id_uses_stable_channel_id() -> None:
    handler = SessionHandler(_Controller(platform="telegram", channel_message_sessions=False))
    context = MessageContext(
        user_id="u-1",
        channel_id="-100123",
        message_id="42",
        platform="telegram",
        platform_specific={"is_dm": False, "chat_type": "supergroup"},
    )

    assert handler.get_base_session_id(context) == "telegram_-100123"
