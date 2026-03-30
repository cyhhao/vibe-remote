import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.message_dispatcher import ConsolidatedMessageDispatcher
from core.reply_enhancer import build_reply_enhancements_prompt, process_reply
from modules.im import MessageContext


class _StubSettingsManager:
    @staticmethod
    def _canonicalize_message_type(message_type: str) -> str:
        return message_type

    @staticmethod
    def is_message_type_hidden(settings_key: str, message_type: str) -> bool:
        return False


class _StubIMClient:
    def __init__(self):
        self.sent_messages = []
        self.sent_button_messages = []

    @staticmethod
    def should_use_thread_for_reply() -> bool:
        return False

    async def send_message(self, context, text, parse_mode=None, reply_to=None):
        self.sent_messages.append((context.channel_id, text, parse_mode))
        return "msg-1"

    async def send_message_with_buttons(self, context, text, keyboard, parse_mode=None):
        self.sent_button_messages.append((context.channel_id, text, parse_mode, keyboard))
        return "btn-1"


class _StubController:
    def __init__(self, platform: str):
        self.config = type(
            "Config",
            (),
            {"platform": platform, "reply_enhancements": True},
        )()
        self.settings_manager = _StubSettingsManager()
        self.im_client = _StubIMClient()

    @staticmethod
    def _get_settings_key(context: MessageContext) -> str:
        return f"{context.channel_id}:{context.user_id}"

    @staticmethod
    def _get_session_key(context: MessageContext) -> str:
        return f"{getattr(context, 'platform', None) or 'test'}::{context.channel_id}:{context.user_id}"

    def get_settings_manager_for_context(self, context=None):
        return self.settings_manager


class ReplyEnhancerPlatformTests(unittest.IsolatedAsyncioTestCase):
    def test_prompt_can_exclude_quick_replies(self):
        prompt = build_reply_enhancements_prompt(include_quick_replies=False)

        self.assertIn("## 1. Send files", prompt)
        self.assertNotIn("## 2. Quick-reply buttons", prompt)
        self.assertIn("https://github.com/cyhhao/vibe-remote/raw/master/skills/use-vibe-remote/SKILL.md", prompt)

    def test_prompt_includes_scheduled_task_usage_with_threadless_default_session_key(self):
        context = MessageContext(
            user_id="U1",
            channel_id="C1",
            platform="slack",
            thread_id="171717.123",
            platform_specific={"is_dm": False},
        )

        prompt = build_reply_enhancements_prompt(include_quick_replies=True, context=context)

        self.assertIn("## 3. Scheduled tasks", prompt)
        self.assertIn("`vibe task add`", prompt)
        self.assertIn("Default session key: `slack::channel::C1`", prompt)
        self.assertIn("Current thread ID: `171717.123`", prompt)
        self.assertIn("slack::channel::C1::thread::171717.123", prompt)
        self.assertIn("https://github.com/cyhhao/vibe-remote/raw/master/skills/use-vibe-remote/SKILL.md", prompt)

    def test_prompt_uses_fallback_platform_for_unannotated_context(self):
        context = MessageContext(
            user_id="U1",
            channel_id="C1",
            thread_id="171717.123",
            platform_specific={"is_dm": False},
        )

        prompt = build_reply_enhancements_prompt(
            include_quick_replies=True,
            context=context,
            fallback_platform="slack",
        )

        self.assertIn("Default session key: `slack::channel::C1`", prompt)

    def test_file_links_with_parentheses_are_preserved(self):
        enhanced = process_reply("![video](file:///Users/test/SaveTwitter.Net_GABV3XNWYAARAZz(gif).mp4)")

        self.assertEqual(len(enhanced.files), 1)
        self.assertEqual(
            enhanced.files[0].path,
            "/Users/test/SaveTwitter.Net_GABV3XNWYAARAZz(gif).mp4",
        )

    async def test_wechat_result_ignores_quick_reply_buttons(self):
        controller = _StubController("wechat")
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(user_id="U1", channel_id="C1")

        await dispatcher.emit_agent_message(
            context,
            "result",
            "Done.\n---\n[继续] | [提交PR]",
        )

        self.assertEqual(controller.im_client.sent_button_messages, [])
        self.assertEqual(
            controller.im_client.sent_messages,
            [("C1", "Done.", "markdown")],
        )

    async def test_lark_quick_reply_buttons_use_vertical_layout(self):
        controller = _StubController("lark")
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(user_id="U1", channel_id="C1", platform="lark")

        await dispatcher.emit_agent_message(
            context,
            "result",
            "Done.\n---\n[继续] | [提交PR]",
        )

        self.assertEqual(len(controller.im_client.sent_button_messages), 1)
        keyboard = controller.im_client.sent_button_messages[0][3]
        self.assertEqual([[button.text for button in row] for row in keyboard.buttons], [["继续"], ["提交PR"]])


if __name__ == "__main__":
    unittest.main()
