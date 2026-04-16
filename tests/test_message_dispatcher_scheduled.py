from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.message_dispatcher import ConsolidatedMessageDispatcher
from modules.im import MessageContext


class _StubIMClient:
    def __init__(self):
        self.sent = []
        self._next_id = 1

    def should_use_thread_for_reply(self):
        return False

    async def send_message(self, context, text, parse_mode=None, reply_to=None):
        self.sent.append((context.channel_id, context.thread_id, text))
        message_id = f"bot-msg-{self._next_id}"
        self._next_id += 1
        return message_id

    async def send_message_with_buttons(self, context, text, keyboard, parse_mode=None):
        message_id = f"bot-msg-{self._next_id}"
        self._next_id += 1
        return message_id


class _StubSettingsManager:
    def _canonicalize_message_type(self, message_type):
        return message_type

    def is_message_type_hidden(self, settings_key, canonical_type):
        return False


class _StubSessionHandler:
    def __init__(self):
        self.calls = []

    def finalize_scheduled_delivery(self, context, sent_message_id):
        self.calls.append((context.channel_id, context.thread_id, sent_message_id))


class _StubController:
    def __init__(self):
        self.config = type("Config", (), {"platform": "slack", "reply_enhancements": False})()
        self.session_handler = _StubSessionHandler()
        self.im_client = _StubIMClient()

    def _get_settings_key(self, context):
        return context.channel_id

    def _get_session_key(self, context):
        return f"slack::{context.channel_id}"

    def get_settings_manager_for_context(self, context):
        return _StubSettingsManager()

    def get_im_client_for_context(self, context):
        return self.im_client


class MessageDispatcherScheduledTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_message_finalizes_scheduled_delivery(self):
        controller = _StubController()
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(
            user_id="scheduled",
            channel_id="C123",
            platform="slack",
            platform_specific={
                "turn_source": "scheduled",
                "turn_base_session_id": "slack_scheduled-1",
                "scheduled_anchor_required": True,
            },
        )

        message_id = await dispatcher.emit_agent_message(context, "result", "hello")

        self.assertEqual(message_id, "bot-msg-1")
        self.assertEqual(controller.im_client.sent, [("C123", None, "hello")])
        self.assertEqual(controller.session_handler.calls, [("C123", None, "bot-msg-1")])

    async def test_result_message_strips_silent_blocks(self):
        controller = _StubController()
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(user_id="U1", channel_id="C123", platform="slack")

        message_id = await dispatcher.emit_agent_message(
            context,
            "result",
            "<silent>internal decision</silent>\nVisible reply",
        )

        self.assertEqual(message_id, "bot-msg-1")
        self.assertEqual(controller.im_client.sent, [("C123", None, "Visible reply")])

    async def test_silent_only_result_sends_nothing(self):
        controller = _StubController()
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(user_id="U1", channel_id="C123", platform="slack")

        message_id = await dispatcher.emit_agent_message(
            context,
            "result",
            "<silent>not relevant to the bot</silent>",
        )

        self.assertIsNone(message_id)
        self.assertEqual(controller.im_client.sent, [])
        self.assertEqual(controller.session_handler.calls, [])

    async def test_silent_only_log_message_sends_nothing(self):
        controller = _StubController()
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(user_id="U1", channel_id="C123", platform="slack")

        message_id = await dispatcher.emit_agent_message(
            context,
            "assistant",
            "<silent>only internal note</silent>",
        )

        self.assertIsNone(message_id)
        self.assertEqual(controller.im_client.sent, [])

    async def test_delivery_override_sends_result_to_parent_channel(self):
        controller = _StubController()
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(
            user_id="scheduled",
            channel_id="C123",
            thread_id="171717.123",
            platform="slack",
            platform_specific={
                "turn_source": "scheduled",
                "turn_base_session_id": "slack_171717.123",
                "delivery_override": {
                    "user_id": "scheduled",
                    "channel_id": "C123",
                    "thread_id": None,
                    "platform": "slack",
                    "is_dm": False,
                },
                "scheduled_delivery_alias": {
                    "mode": "sent_message",
                    "session_key": "slack::C123",
                    "clear_source": False,
                },
            },
        )

        message_id = await dispatcher.emit_agent_message(context, "result", "hello")

        self.assertEqual(message_id, "bot-msg-1")
        self.assertEqual(controller.im_client.sent, [("C123", None, "hello")])
        self.assertEqual(controller.session_handler.calls, [("C123", "171717.123", "bot-msg-1")])

    async def test_discord_long_result_uses_first_chunk_as_scheduled_anchor(self):
        controller = _StubController()
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(
            user_id="scheduled",
            channel_id="C123",
            platform="discord",
            platform_specific={
                "turn_source": "scheduled",
                "turn_base_session_id": "discord_scheduled-1",
                "scheduled_anchor_required": True,
            },
        )
        long_text = "x" * 4200

        message_id = await dispatcher.emit_agent_message(context, "result", long_text)

        self.assertEqual(message_id, "bot-msg-1")
        self.assertEqual(len(controller.im_client.sent), 3)
        self.assertEqual("".join(text for _, _, text in controller.im_client.sent), long_text)
        self.assertEqual(controller.session_handler.calls, [("C123", None, "bot-msg-1")])
