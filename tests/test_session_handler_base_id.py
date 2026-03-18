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
    def __init__(self) -> None:
        self.config = _Config()
        self.im_client = type("IM", (), {"formatter": None})()
        self.settings_manager = type("Settings", (), {"sessions": None})()
        self.session_manager = object()
        self.claude_sessions = {}
        self.receiver_tasks = {}
        self.stored_session_mappings = {}

    def get_cwd(self, context: MessageContext) -> str:
        return "/tmp/workdir"

    def _get_settings_key(self, context: MessageContext) -> str:
        return context.user_id if (context.platform_specific or {}).get("is_dm") else context.channel_id


def test_dm_session_base_id_uses_stable_channel_id() -> None:
    handler = SessionHandler(_Controller())
    context = MessageContext(
        user_id="u-1",
        channel_id="dm-123",
        message_id="msg-999",
        platform_specific={"is_dm": True},
    )

    assert handler.get_base_session_id(context) == "discord_dm-123"


def test_channel_session_base_id_keeps_thread_or_message_behavior() -> None:
    handler = SessionHandler(_Controller())
    context = MessageContext(
        user_id="u-1",
        channel_id="chan-123",
        message_id="msg-999",
        platform_specific={"is_dm": False},
    )

    assert handler.get_base_session_id(context) == "discord_msg-999"
