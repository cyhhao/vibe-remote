from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
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


def test_pending_cwd_prompt_consumes_next_plain_message() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        thread_id="1",
        platform="telegram",
        platform_specific={"is_dm": False},
    )
    bot._cwd_prompts[bot._interaction_scope_key(context)] = SimpleNamespace(message_id="10", current_cwd="/tmp")
    bot._controller = SimpleNamespace(
        command_handler=SimpleNamespace(handle_set_cwd=AsyncMock()),
    )

    handled = asyncio.run(bot._consume_cwd_prompt(context, "/repo/new"))

    assert handled is True
    bot._controller.command_handler.handle_set_cwd.assert_awaited_once()
    assert bot._interaction_scope_key(context) not in bot._cwd_prompts


def test_resume_menu_uses_short_callback_ids() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        thread_id="1",
        platform="telegram",
        platform_specific={"is_dm": False},
    )

    with patch.object(bot, "send_message_with_buttons", new=AsyncMock(return_value="55")) as send_mock:
        asyncio.run(
            bot.open_resume_session_modal(
                context,
                {"codex": {"thread-a": "session_abcdefghijklmnopqrstuvwxyz"}},
                context.channel_id,
                context.thread_id,
                context.message_id,
            )
        )

    keyboard = send_mock.await_args.args[2]
    assert keyboard.buttons[0][0].callback_data == "tg_resume:0"
    state = bot._resume_states[bot._interaction_scope_key(context)]
    assert state.options == [("codex", "session_abcdefghijklmnopqrstuvwxyz")]


def test_resume_callback_submits_selected_session() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        thread_id="1",
        message_id="55",
        platform="telegram",
        platform_specific={"is_dm": False},
    )
    bot._resume_states[bot._interaction_scope_key(context)] = SimpleNamespace(
        message_id="55",
        options=[("claude", "sess_123")],
        is_dm=False,
    )
    bot._controller = SimpleNamespace(
        session_handler=SimpleNamespace(handle_resume_session_submission=AsyncMock()),
    )

    with patch.object(bot, "edit_message", new=AsyncMock(return_value=True)):
        asyncio.run(bot._handle_resume_callback(context, "tg_resume:0"))

    bot._controller.session_handler.handle_resume_session_submission.assert_awaited_once()


def test_routing_callback_save_persists_selected_backend() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        message_id="88",
        platform="telegram",
        platform_specific={"is_dm": False},
    )
    bot._routing_states[bot._interaction_scope_key(context)] = SimpleNamespace(
        message_id="88",
        channel_id=context.channel_id,
        user_id=context.user_id,
        is_dm=False,
        backend="codex",
        opencode_agent=None,
        opencode_model=None,
        opencode_reasoning_effort=None,
        claude_agent=None,
        claude_model=None,
        claude_reasoning_effort=None,
        codex_model="gpt-5",
        codex_reasoning_effort="high",
        picker_field=None,
        picker_page=0,
    )
    bot._controller = SimpleNamespace(
        settings_handler=SimpleNamespace(handle_routing_update=AsyncMock()),
    )

    with patch.object(bot, "edit_message", new=AsyncMock(return_value=True)):
        asyncio.run(bot._handle_routing_callback(context, "tg_route:save"))

    bot._controller.settings_handler.handle_routing_update.assert_awaited_once()


def test_open_question_modal_edits_message_with_telegram_buttons() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        message_id="99",
        platform="telegram",
        platform_specific={"is_dm": False},
    )
    pending = SimpleNamespace(
        questions=[
            SimpleNamespace(
                header="Backend",
                question="Pick a backend",
                options=[SimpleNamespace(label="Codex", description=""), SimpleNamespace(label="Claude", description="")],
                multiple=False,
            ),
            SimpleNamespace(
                header="Reasoning",
                question="Pick reasoning",
                options=[SimpleNamespace(label="High", description="")],
                multiple=False,
            ),
        ]
    )

    with patch.object(bot, "edit_message", new=AsyncMock(return_value=True)) as edit_mock:
        asyncio.run(bot.open_question_modal(context, context, pending, "claude_question"))

    assert bot._question_states[bot._interaction_scope_key(context)].message_id == "99"
    keyboard = edit_mock.await_args.kwargs["keyboard"]
    assert keyboard.buttons[0][0].callback_data == "tg_question:choose:1"


def test_question_callback_finalizes_with_synthetic_modal_payload() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        message_id="99",
        platform="telegram",
        platform_specific={"is_dm": False},
    )
    bot._question_states[bot._interaction_scope_key(context)] = SimpleNamespace(
        message_id="99",
        callback_prefix="claude_question",
        questions=[
            SimpleNamespace(
                header="Backend",
                question="Pick a backend",
                options=[SimpleNamespace(label="Codex", description=""), SimpleNamespace(label="Claude", description="")],
                multiple=False,
            )
        ],
        answers=[[]],
        index=0,
    )
    bot.on_callback_query_callback = AsyncMock()

    with patch.object(bot, "edit_message", new=AsyncMock(return_value=True)):
        asyncio.run(bot._handle_question_callback(context, "tg_question:choose:2"))

    bot.on_callback_query_callback.assert_awaited_once()
    forwarded = bot.on_callback_query_callback.await_args.args[1]
    assert forwarded.startswith("claude_question:modal:")
    assert '"Claude"' in forwarded
