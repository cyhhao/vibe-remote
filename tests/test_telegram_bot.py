from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import paths
from config.discovered_chats import DiscoveredChatsStore
from modules.im import MessageContext
from modules.im.telegram import TelegramBot
from config.v2_config import TelegramConfig


def test_normalize_command_text_strips_bot_mention() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    bot._bot_user = {"id": 1, "username": "vibe_remote_bot"}

    assert bot._normalize_command_text("/start@vibe_remote_bot hello") == "/start hello"


def test_plain_group_sessions_are_channel_scoped() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))

    assert bot.should_use_message_id_for_channel_session() is False


def test_forum_general_message_auto_creates_topic() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token", forum_auto_topic=True))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        thread_id="1",
        message_id="77",
        platform="telegram",
        platform_specific={"chat_type": "supergroup", "is_topic_message": True},
    )
    message = {
        "from": {"first_name": "Alex"},
        "is_topic_message": True,
        "message_thread_id": 1,
    }

    with patch(
        "modules.im.telegram.telegram_api.create_forum_topic",
        new=AsyncMock(return_value={"result": {"message_thread_id": 88}}),
    ):
        with patch.object(bot, "send_message", new=AsyncMock(return_value="1")):
            topic_context = asyncio.run(bot.start_new_topic_session(context, seed_text="Investigate this bug", message=message))

    assert topic_context is not None
    assert topic_context.thread_id == "88"


def test_should_auto_create_topic_only_for_general_topic_messages() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token", forum_auto_topic=True))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        thread_id="1",
        message_id="77",
        platform="telegram",
        platform_specific={"chat_type": "supergroup"},
    )

    assert bot._should_auto_create_topic(context, {"is_topic_message": True}, "hello") is True
    assert bot._should_auto_create_topic(context, {"is_topic_message": True, "reply_to_message": {"message_id": 1}}, "hello") is False
    assert bot._should_auto_create_topic(context, {"is_topic_message": True}, "/start") is False


def test_build_message_context_records_discovered_chat(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    DiscoveredChatsStore.reset_instance()
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))

    context = bot._build_message_context(
        {
            "message_id": 77,
            "chat": {"id": -100123, "type": "supergroup", "title": "Core Forum"},
            "from": {"id": 42},
            "is_topic_message": True,
            "message_thread_id": 1,
        }
    )

    assert context is not None
    chats = DiscoveredChatsStore.get_instance().list_chats("telegram", include_private=False)
    assert len(chats) == 1
    assert chats[0].chat_id == "-100123"
    assert chats[0].is_forum is True
    DiscoveredChatsStore.reset_instance()
