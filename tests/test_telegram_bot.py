from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import paths
from config.discovered_chats import DiscoveredChatsStore
from modules.agents.native_sessions import NativeResumeSession
from modules.im import MessageContext
from modules.im.telegram import TelegramBot
from config.v2_config import TelegramConfig


def test_normalize_command_text_strips_bot_mention() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    bot._bot_user = {"id": 1, "username": "vibe_remote_bot"}

    assert bot._normalize_command_text("/start@vibe_remote_bot hello") == "/start hello"


def test_strip_leading_bot_mention_returns_empty_for_mention_only() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    bot._bot_user = {"id": 1, "username": "vibe_remote_bot"}
    message = {
        "text": "@vibe_remote_bot",
        "entities": [{"type": "mention", "offset": 0, "length": 16}],
    }

    assert bot._strip_leading_bot_mention(message, "@vibe_remote_bot") == ""


def test_strip_leading_bot_mention_keeps_message_body() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    bot._bot_user = {"id": 1, "username": "vibe_remote_bot"}
    message = {
        "text": "@vibe_remote_bot hello there",
        "entities": [{"type": "mention", "offset": 0, "length": 16}],
    }

    assert bot._strip_leading_bot_mention(message, "@vibe_remote_bot hello there") == "hello there"


def test_group_message_uses_channel_require_mention_override() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token", require_mention=True))
    bot._bot_user = {"id": 1, "username": "vibe_remote_bot"}
    bot.settings_manager = SimpleNamespace(get_require_mention=lambda channel_id, global_default=False: False)
    bot.on_message_callback = AsyncMock()

    asyncio.run(
        bot._handle_message(
            {
                "message_id": 77,
                "chat": {"id": -100123, "type": "group", "title": "Core Group"},
                "from": {"id": 42},
                "text": "hello team",
            }
        )
    )

    bot.on_message_callback.assert_awaited_once()
    assert bot.on_message_callback.await_args.args[1] == "hello team"


def test_group_mention_only_falls_through_as_empty_message() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token", require_mention=True))
    bot._bot_user = {"id": 1, "username": "vibe_remote_bot"}
    bot.on_message_callback = AsyncMock()
    message = {
        "message_id": 77,
        "chat": {"id": -100123, "type": "group", "title": "Core Group"},
        "from": {"id": 42},
        "text": "@vibe_remote_bot",
        "entities": [{"type": "mention", "offset": 0, "length": 16}],
    }

    asyncio.run(bot._handle_message(message))

    bot.on_message_callback.assert_awaited_once()
    assert bot.on_message_callback.await_args.args[1] == ""


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
        with patch.object(bot, "send_message", new=AsyncMock(return_value="1")) as send_mock:
            topic_context = asyncio.run(bot.start_new_topic_session(context, seed_text="Investigate this bug", message=message))

    assert topic_context is not None
    assert topic_context.thread_id == "88"
    send_mock.assert_awaited_once()
    assert send_mock.await_args.args[0] == context


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


def test_should_auto_create_topic_for_forum_general_without_thread_id() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token", forum_auto_topic=True))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        thread_id=None,
        message_id="77",
        platform="telegram",
        platform_specific={"chat_type": "supergroup", "is_forum": True, "is_topic_message": False},
    )

    message = {
        "chat": {"id": -100123, "type": "supergroup", "title": "Core Forum", "is_forum": True},
        "message_id": 77,
    }

    assert bot._should_auto_create_topic(context, message, "hello from general") is True


def test_should_not_auto_create_topic_for_empty_general_service_update() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token", forum_auto_topic=True))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        thread_id=None,
        message_id="77",
        platform="telegram",
        platform_specific={"chat_type": "supergroup", "is_forum": True, "is_topic_message": False},
    )
    message = {
        "chat": {"id": -100123, "type": "supergroup", "title": "Core Forum", "is_forum": True},
        "message_id": 77,
        "pinned_message": {"message_id": 12},
    }

    assert bot._should_auto_create_topic(context, message, "") is False


def test_should_auto_create_topic_for_general_photo_without_text() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token", forum_auto_topic=True))
    message = {
        "chat": {"id": -100123, "type": "supergroup", "title": "Core Forum", "is_forum": True},
        "message_id": 77,
        "photo": [{"file_id": "small"}, {"file_id": "large", "file_size": 42}],
    }
    context = bot._build_message_context(
        {
            **message,
            "from": {"id": 42},
        }
    )

    assert context is not None
    assert bot._should_auto_create_topic(context, message, "") is True


def test_start_new_topic_session_allows_forum_context_without_thread_id() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token", forum_auto_topic=True))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        thread_id=None,
        message_id="77",
        platform="telegram",
        platform_specific={"chat_type": "supergroup", "is_forum": True, "is_topic_message": False},
    )
    message = {
        "chat": {"id": -100123, "type": "supergroup", "title": "Core Forum", "is_forum": True},
        "from": {"first_name": "Alex"},
        "message_id": 77,
    }

    with patch(
        "modules.im.telegram.telegram_api.create_forum_topic",
        new=AsyncMock(return_value={"result": {"message_thread_id": 88}}),
    ):
        with patch.object(bot, "send_message", new=AsyncMock(return_value="1")) as send_mock:
            topic_context = asyncio.run(bot.start_new_topic_session(context, seed_text="Investigate this bug", message=message))

    assert topic_context is not None
    assert topic_context.thread_id == "88"
    send_mock.assert_awaited_once()
    assert send_mock.await_args.args[0] == context


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


def test_handle_message_ignores_foreign_bot_command() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    bot._bot_user = {"id": 1, "username": "vibe_remote_bot"}
    bot.on_message_callback = AsyncMock()
    bot.on_command_callbacks["start"] = AsyncMock()

    asyncio.run(
        bot._handle_message(
            {
                "message_id": 77,
                "chat": {"id": -100123, "type": "supergroup", "title": "Core Forum"},
                "from": {"id": 42},
                "text": "/start@other_bot hello",
            }
        )
    )

    bot.on_command_callbacks["start"].assert_not_awaited()
    bot.on_message_callback.assert_not_awaited()


def test_run_dispatches_telegram_updates_concurrently() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    started: list[int] = []
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    poll_calls = 0

    async def fake_get_updates(_token: str, _offset=None):
        nonlocal poll_calls
        poll_calls += 1
        if poll_calls == 1:
            return {"result": [{"update_id": 1}, {"update_id": 2}]}
        await asyncio.sleep(0)
        return {"result": []}

    async def fake_handle(update: dict[str, int]) -> None:
        started.append(update["update_id"])
        if update["update_id"] == 2:
            second_started.set()
            release_first.set()
            bot.stop()
        await release_first.wait()

    with patch("modules.im.telegram.telegram_api.get_me", new=AsyncMock(return_value={"result": {"username": "bot"}})):
        with patch("modules.im.telegram.telegram_api.get_updates", new=AsyncMock(side_effect=fake_get_updates)):
            with patch.object(bot, "_handle_update", new=fake_handle):
                asyncio.run(asyncio.wait_for(bot._run(), timeout=0.2))

    assert second_started.is_set()
    assert started == [1, 2]


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

    with patch.object(bot, "_delete_interaction_message", new=AsyncMock()) as delete_mock:
        handled = asyncio.run(bot._consume_cwd_prompt(context, "/repo/new"))

    assert handled is True
    bot._controller.command_handler.handle_set_cwd.assert_awaited_once()
    delete_mock.assert_awaited_once_with(context, "10")
    assert bot._interaction_scope_key(context) not in bot._cwd_prompts


def test_pending_cwd_prompt_bypasses_slash_command_with_args() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        thread_id="1",
        platform="telegram",
        platform_specific={"is_dm": False},
    )
    bot._cwd_prompts[bot._interaction_scope_key(context)] = SimpleNamespace(message_id="10", current_cwd="/tmp")

    handled = asyncio.run(bot._consume_cwd_prompt(context, "/resume codex:abc"))

    assert handled is False
    assert bot._interaction_scope_key(context) in bot._cwd_prompts


def test_send_message_uses_html_parse_mode() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(user_id="42", channel_id="-100123", platform="telegram")

    with patch(
        "modules.im.telegram.telegram_api.call_api",
        new=AsyncMock(return_value={"result": {"message_id": 77}}),
    ) as call_mock:
        asyncio.run(bot.send_message(context, "Hello **world**"))

    payload = call_mock.await_args.args[2]
    assert payload["parse_mode"] == "HTML"
    assert payload["text"] == "Hello <b>world</b>"


def test_add_reaction_uses_telegram_message_reactions() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(user_id="42", channel_id="-100123", platform="telegram")

    with patch(
        "modules.im.telegram.telegram_api.set_message_reaction",
        new=AsyncMock(return_value={"ok": True}),
    ) as reaction_mock:
        result = asyncio.run(bot.add_reaction(context, "77", ":eyes:"))

    assert result is True
    reaction_mock.assert_awaited_once_with("123456:test-token", "-100123", "77", "👀")


def test_remove_reaction_clears_telegram_message_reactions() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(user_id="42", channel_id="-100123", platform="telegram")

    with patch(
        "modules.im.telegram.telegram_api.clear_message_reaction",
        new=AsyncMock(return_value={"ok": True}),
    ) as reaction_mock:
        result = asyncio.run(bot.remove_reaction(context, "77", ":eyes:"))

    assert result is True
    reaction_mock.assert_awaited_once_with("123456:test-token", "-100123", "77")


def test_resume_menu_uses_short_callback_ids() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        thread_id="1",
        platform="telegram",
        platform_specific={"is_dm": False},
    )
    sessions = [
        NativeResumeSession(
            agent="codex",
            agent_prefix="cx",
            native_session_id="session_abcdefghijklmnopqrstuvwxyz",
            working_path="/Users/cyh/vibe-remote",
            created_at=None,
            updated_at=None,
            sort_ts=100.0,
            last_agent_message="Latest answer",
            last_agent_tail="...Latest answer",
        )
    ]

    with patch.object(bot, "send_message_with_buttons", new=AsyncMock(return_value="55")) as send_mock:
        asyncio.run(
            bot.open_resume_session_modal(
                context,
                sessions,
                context.channel_id,
                context.thread_id,
                context.message_id,
            )
        )

    text = send_mock.await_args.args[1]
    keyboard = send_mock.await_args.args[2]
    assert keyboard.buttons[0][0].callback_data == "tg_resume:0"
    state = bot._resume_states[bot._interaction_scope_key(context)]
    assert state.options == [("codex", "session_abcdefghijklmnopqrstuvwxyz")]
    assert "cx...Latest answer" in text


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

    with patch.object(bot, "_delete_interaction_message", new=AsyncMock()) as delete_mock:
        asyncio.run(bot._handle_routing_callback(context, "tg_route:save"))

    delete_mock.assert_awaited_once_with(context, "88")
    bot._controller.settings_handler.handle_routing_update.assert_awaited_once()


def test_routing_state_marks_current_backend_in_first_row() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    state = SimpleNamespace(
        registered_backends=["opencode", "claude", "codex"],
        backend="claude",
        opencode_agent=None,
        opencode_model=None,
        opencode_reasoning_effort=None,
        claude_agent="reviewer",
        claude_model="claude-sonnet-4-6",
        claude_reasoning_effort="high",
        codex_model=None,
        codex_reasoning_effort=None,
        picker_field=None,
        picker_page=0,
    )

    _, keyboard = bot._render_routing_state(state)

    assert [button.callback_data for button in keyboard.buttons[0]] == [
        "tg_route:backend:opencode",
        "tg_route:backend:claude",
        "tg_route:backend:codex",
    ]
    assert keyboard.buttons[0][1].text.startswith("☑️ ")


def test_routing_callback_backend_switches_without_nested_picker() -> None:
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
        registered_backends=["opencode", "claude", "codex"],
        backend="opencode",
        opencode_agent=None,
        opencode_model=None,
        opencode_reasoning_effort=None,
        claude_agent=None,
        claude_model=None,
        claude_reasoning_effort=None,
        codex_model=None,
        codex_reasoning_effort=None,
        picker_field=None,
        picker_page=0,
    )

    with patch.object(bot, "edit_message", new=AsyncMock(return_value=True)) as edit_mock:
        asyncio.run(bot._handle_routing_callback(context, "tg_route:backend:claude"))

    state = bot._routing_states[bot._interaction_scope_key(context)]
    assert state.backend == "claude"
    assert state.picker_field is None
    edit_mock.assert_awaited_once()


def test_routing_state_keeps_backend_picker_entry_for_extra_backends() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    state = SimpleNamespace(
        registered_backends=["opencode", "claude", "codex", "extra"],
        backend="extra",
        opencode_agent=None,
        opencode_model=None,
        opencode_reasoning_effort=None,
        claude_agent=None,
        claude_model=None,
        claude_reasoning_effort=None,
        codex_model=None,
        codex_reasoning_effort=None,
        picker_field=None,
        picker_page=0,
    )

    _, keyboard = bot._render_routing_state(state)

    assert [button.callback_data for button in keyboard.buttons[0]] == [
        "tg_route:backend:opencode",
        "tg_route:backend:claude",
        "tg_route:backend:codex",
    ]
    assert keyboard.buttons[1][0].callback_data == "tg_route:field:backend"


def test_routing_callback_backend_picker_can_select_extra_backend() -> None:
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
        registered_backends=["opencode", "claude", "codex", "extra"],
        backend="opencode",
        opencode_agent=None,
        opencode_model=None,
        opencode_reasoning_effort=None,
        claude_agent=None,
        claude_model=None,
        claude_reasoning_effort=None,
        codex_model=None,
        codex_reasoning_effort=None,
        picker_field=None,
        picker_page=0,
    )

    with patch.object(bot, "edit_message", new=AsyncMock(return_value=True)) as edit_mock:
        asyncio.run(bot._handle_routing_callback(context, "tg_route:field:backend"))
        asyncio.run(bot._handle_routing_callback(context, "tg_route:option:3"))

    state = bot._routing_states[bot._interaction_scope_key(context)]
    assert state.backend == "extra"
    assert state.picker_field is None
    assert edit_mock.await_count == 2


def test_open_settings_modal_includes_language_buttons() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        message_id="88",
        platform="telegram",
        platform_specific={"is_dm": False},
    )
    user_settings = SimpleNamespace(show_message_types=["assistant", "toolcall"])

    with patch.object(bot, "send_message_with_buttons", new=AsyncMock(return_value="66")) as send_mock:
        asyncio.run(
            bot.open_settings_modal(
                context,
                user_settings=user_settings,
                message_types=["assistant", "toolcall", "system"],
                display_names={},
                channel_id=context.channel_id,
                current_require_mention=None,
                global_require_mention=True,
                current_language="en",
            )
        )

    keyboard = send_mock.await_args.args[2]
    language_row = keyboard.buttons[-3]
    assert [button.callback_data for button in language_row] == ["tg_settings:lang:en", "tg_settings:lang:zh"]


def test_render_settings_state_localizes_current_label() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    bot._controller = SimpleNamespace(_get_lang=lambda: "en")

    text, _ = bot._render_settings_state(
        SimpleNamespace(
            show_message_types=["assistant"],
            current_require_mention=None,
            global_require_mention=True,
            current_language="en",
        ),
        ["assistant", "toolcall"],
    )

    assert "Current:" in text
    assert "当前:" not in text


def test_settings_callback_save_updates_language_and_deletes_menu() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    context = MessageContext(
        user_id="42",
        channel_id="-100123",
        message_id="66",
        platform="telegram",
        platform_specific={"is_dm": False},
    )
    bot.settings_manager = SimpleNamespace(get_available_message_types=lambda: ["assistant", "toolcall", "system"])
    bot._settings_states[bot._interaction_scope_key(context)] = SimpleNamespace(
        message_id="66",
        show_message_types=["assistant"],
        current_require_mention=None,
        global_require_mention=True,
        current_language="zh",
        is_dm=False,
    )
    bot._controller = SimpleNamespace(
        settings_handler=SimpleNamespace(handle_settings_update=AsyncMock()),
    )

    with patch.object(bot, "_delete_interaction_message", new=AsyncMock()) as delete_mock:
        asyncio.run(bot._handle_settings_callback(context, "tg_settings:save"))

    delete_mock.assert_awaited_once_with(context, "66")
    bot._controller.settings_handler.handle_settings_update.assert_awaited_once_with(
        user_id="42",
        show_message_types=["assistant"],
        channel_id="-100123",
        require_mention=None,
        language="zh",
        notify_user=True,
        is_dm=False,
        platform="telegram",
    )


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


def test_handle_callback_query_denies_unauthorized_protected_action() -> None:
    bot = TelegramBot(TelegramConfig(bot_token="123456:test-token"))
    bot.check_authorization = lambda **kwargs: SimpleNamespace(allowed=False, denial="not_admin")
    bot.build_auth_denial_text = lambda denial, channel_id=None: "Admin only"
    bot.on_callback_query_callback = AsyncMock()

    with patch.object(bot, "answer_callback", new=AsyncMock(return_value=True)) as answer_mock:
        asyncio.run(
            bot._handle_callback_query(
                {
                    "id": "cb-1",
                    "data": "cmd_settings",
                    "from": {"id": 42},
                    "message": {
                        "message_id": 99,
                        "chat": {"id": -100123, "type": "supergroup", "title": "Core Forum"},
                    },
                }
            )
        )

    answer_mock.assert_awaited_once_with("cb-1", "Admin only", show_alert=True)
    bot.on_callback_query_callback.assert_not_awaited()
