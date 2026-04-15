from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.message_dispatcher import ConsolidatedMessageDispatcher
from modules.im import MessageContext


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


class _StubIMClient:
    def __init__(self, *, fail_first_send: bool = False):
        self.sent_messages = []
        self.uploaded_markdowns = []
        self._next_id = 1
        self._fail_first_send = fail_first_send
        self._send_attempts = 0

    def should_use_thread_for_reply(self):
        return False

    async def send_message(self, context, text, parse_mode=None, reply_to=None):
        self._send_attempts += 1
        if self._fail_first_send and self._send_attempts == 1:
            raise RuntimeError("inline send failed")
        self.sent_messages.append((context.channel_id, text, parse_mode))
        message_id = f"msg-{self._next_id}"
        self._next_id += 1
        return message_id

    async def upload_markdown(self, context, title, content, filetype="markdown"):
        self.uploaded_markdowns.append((context.channel_id, title, content, filetype))
        return "file-1"


class _StubController:
    def __init__(self, *, platform: str = "lark", language: str = "en", fail_first_send: bool = False):
        self.config = type(
            "Config",
            (),
            {"platform": platform, "language": language, "reply_enhancements": False},
        )()
        self.session_handler = _StubSessionHandler()
        self.im_client = _StubIMClient(fail_first_send=fail_first_send)

    def _get_settings_key(self, context):
        return context.channel_id

    def _get_session_key(self, context):
        return f"{context.platform}::{context.channel_id}"

    def get_settings_manager_for_context(self, context):
        return _StubSettingsManager()

    def get_im_client_for_context(self, context):
        return self.im_client


class MessageDispatcherResultFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_upload_becomes_primary_anchor_without_duplicate_upload(self):
        controller = _StubController(platform="lark", language="en", fail_first_send=True)
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(user_id="U1", channel_id="C1", platform="lark")
        long_text = "x" * 35000

        message_id = await dispatcher.emit_agent_message(context, "result", long_text)

        self.assertEqual(message_id, "file-1")
        self.assertEqual(
            controller.im_client.uploaded_markdowns,
            [("C1", "result.md", long_text, "markdown")],
        )
        self.assertEqual(
            controller.im_client.sent_messages,
            [("C1", "⚠️ The message could not be sent inline, so I sent it as `result.md` above.", "markdown")],
        )
        self.assertEqual(controller.session_handler.calls, [("C1", None, "file-1")])

    async def test_attachment_only_notice_uses_configured_language(self):
        controller = _StubController(platform="lark", language="zh", fail_first_send=True)
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(user_id="U1", channel_id="C1", platform="lark")

        message_id = await dispatcher.emit_agent_message(context, "result", "| A | B |\n| - | - |\n| 1 | 2 |")

        self.assertEqual(message_id, "file-1")
        self.assertEqual(
            controller.im_client.uploaded_markdowns,
            [("C1", "result.md", "| A | B |\n| - | - |\n| 1 | 2 |", "markdown")],
        )
        self.assertEqual(
            controller.im_client.sent_messages,
            [("C1", "⚠️ 这条消息无法以内联形式发送，所以我已将完整内容作为 `result.md` 发在上方。", "markdown")],
        )
        self.assertEqual(controller.session_handler.calls, [("C1", None, "file-1")])


if __name__ == "__main__":
    unittest.main()
