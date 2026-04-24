import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.message_dispatcher import ConsolidatedMessageDispatcher
from core.reply_enhancer import build_reply_enhancements_prompt, process_reply
from config import paths
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
        self.uploaded_markdowns = []
        self._next_id = 1

    @staticmethod
    def should_use_thread_for_reply() -> bool:
        return False

    async def send_message(self, context, text, parse_mode=None, reply_to=None):
        self.sent_messages.append((context.channel_id, text, parse_mode))
        message_id = f"msg-{self._next_id}"
        self._next_id += 1
        return message_id

    async def send_message_with_buttons(self, context, text, keyboard, parse_mode=None):
        self.sent_button_messages.append((context.channel_id, text, parse_mode, keyboard))
        message_id = f"btn-{self._next_id}"
        self._next_id += 1
        return message_id

    async def upload_markdown(self, context, title, content, filetype="markdown"):
        self.uploaded_markdowns.append((context.channel_id, title, content, filetype))
        return "file-1"


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
        with patch.object(paths, "get_user_preferences_path", return_value=Path("/tmp/user_preferences.md")):
            prompt = build_reply_enhancements_prompt(include_quick_replies=False)

        self.assertIn("## Silent replies", prompt)
        self.assertIn("<silent>reason not shown to the user</silent>", prompt)
        self.assertIn(
            "If the user asks you to configure, repair, or operate Vibe Remote itself, read `https://github.com/cyhhao/vibe-remote/raw/master/skills/use-vibe-remote/SKILL.md` before making changes.",
            prompt,
        )
        self.assertIn("## 1. Send files", prompt)
        self.assertIn("Vibe Remote provides optional capabilities:", prompt)
        self.assertNotIn("If you generate an image with Codex", prompt)
        self.assertNotIn("## 2. Quick-reply buttons", prompt)
        self.assertIn("## 4. User Context and Preferences", prompt)
        self.assertIn("`/tmp/user_preferences.md`", prompt)
        self.assertIn("`<platform>/<user_id>`", prompt)

    def test_process_reply_strips_silent_blocks_before_enhancements(self):
        reply = process_reply(
            "Visible\n<silent>skip [secret](file:///tmp/secret.txt)\n---\n[Hidden]</silent>\nDone"
        )

        self.assertEqual(reply.text, "Visible\n\nDone")
        self.assertEqual(reply.files, [])
        self.assertEqual(reply.buttons, [])

    def test_prompt_includes_task_watch_and_hook_usage_with_thread_default_session_key(self):
        context = MessageContext(
            user_id="U1",
            channel_id="C1",
            platform="slack",
            thread_id="171717.123",
            platform_specific={"is_dm": False},
        )

        with patch.object(paths, "get_user_preferences_path", return_value=Path("/tmp/user_preferences.md")):
            prompt = build_reply_enhancements_prompt(include_quick_replies=True, context=context)

        self.assertIn("## 3. Scheduled tasks, watches, and hooks", prompt)
        self.assertIn("`vibe task add`", prompt)
        self.assertIn("`vibe hook send --session-key ... --prompt ...`", prompt)
        self.assertIn("`vibe watch add`", prompt)
        self.assertIn("Use `vibe task add` for saved work that should run later on a schedule or at one exact time.", prompt)
        self.assertIn(
            "Use `vibe watch add` for managed background waiters that should keep running until a condition is met and then send a follow-up.",
            prompt,
        )
        self.assertIn("Default session key: `slack::channel::C1::thread::171717.123`", prompt)
        self.assertIn("Channel-level session key: `slack::channel::C1`", prompt)
        self.assertIn(
            "When you do not want to keep the current session and instead want to start or reuse a higher-level session, usually use the higher-level session key.",
            prompt,
        )
        self.assertIn(
            "`--post-to` changes the delivery target, not the session scope. Use `--post-to channel` when the session should stay thread-scoped but the follow-up message should be posted to the parent channel.",
            prompt,
        )
        self.assertIn(
            "Use `vibe watch list`, `vibe watch show`, `vibe watch pause`, `vibe watch resume`, and `vibe watch remove` to manage background work after creation.",
            prompt,
        )
        self.assertIn(
            "Prefer `vibe watch add` over ad-hoc `nohup` or shell-detached jobs when the user wants a managed background task.",
            prompt,
        )
        self.assertIn("If `--timezone` is omitted, the task uses the local system timezone at creation time.", prompt)
        self.assertIn(
            "Use `--prompt \"...\"` or `--prompt-file <path>` for task and hook content. Use `--prefix \"...\"` on watches for the follow-up instruction that is prepended before waiter stdout; when both exist, Vibe Remote joins them with a blank line.",
            prompt,
        )
        self.assertIn(
            "If this is your first time using these commands, read `vibe task add --help`, `vibe watch add --help`, or `vibe hook send --help` before creating anything.",
            prompt,
        )
        self.assertIn("A shared user context and preferences file is available at ", prompt)
        self.assertIn("/tmp/user_preferences.md", prompt)
        self.assertIn("From first principles, serving the user better means thinking proactively about how to make full use of the available context", prompt)
        self.assertIn("Use this file proactively when it is helpful", prompt)
        self.assertIn("You do not need to read it for every simple request; but if consulting it could improve personalization, efficiency, or continuity, prefer checking it early.", prompt)
        self.assertIn("usually in the current user's section: `slack/U1`.", prompt)
        self.assertIn("Only record durable, factual, reusable information there.", prompt)
        self.assertIn("Keep entries short, deduplicated, and free of secrets unless the user explicitly asks.", prompt)

    def test_prompt_uses_fallback_platform_for_unannotated_context(self):
        context = MessageContext(
            user_id="U1",
            channel_id="C1",
            thread_id="171717.123",
            platform_specific={"is_dm": False},
        )

        with patch.object(paths, "get_user_preferences_path", return_value=Path("/tmp/user_preferences.md")):
            prompt = build_reply_enhancements_prompt(
                include_quick_replies=True,
                context=context,
                fallback_platform="slack",
            )

        self.assertIn("Default session key: `slack::channel::C1::thread::171717.123`", prompt)
        self.assertIn("Channel-level session key: `slack::channel::C1`", prompt)
        self.assertIn("usually in the current user's section: `slack/U1`.", prompt)

    def test_prompt_handles_missing_platform_specific(self):
        context = MessageContext(
            user_id="U1",
            channel_id="C1",
            platform=None,
            platform_specific=None,
        )

        with patch.object(paths, "get_user_preferences_path", return_value=Path("/tmp/user_preferences.md")):
            prompt = build_reply_enhancements_prompt(
                include_quick_replies=True,
                context=context,
                fallback_platform="slack",
            )

        self.assertIn("usually in the current user's section: `slack/U1`.", prompt)

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

    async def test_lark_log_message_strips_file_links_before_sending(self):
        controller = _StubController("lark")
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(user_id="U1", channel_id="C1", platform="lark")

        await dispatcher.emit_agent_message(
            context,
            "assistant",
            "Preview ready\n\n![screen](file:///tmp/screen-room.png)",
        )

        self.assertEqual(
            controller.im_client.sent_messages,
            [("C1", "Preview ready\n\nscreen", "markdown")],
        )

    async def test_lark_log_message_preserves_button_like_markdown_blocks(self):
        controller = _StubController("lark")
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(user_id="U1", channel_id="C1", platform="lark")

        await dispatcher.emit_agent_message(
            context,
            "assistant",
            "Runbook\n---\n[step one] | [step two]",
        )

        self.assertEqual(
            controller.im_client.sent_messages,
            [("C1", "Runbook\n---\n[step one] | [step two]", "markdown")],
        )

    async def test_telegram_quick_reply_buttons_use_vertical_layout(self):
        controller = _StubController("telegram")
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(user_id="U1", channel_id="C1", platform="telegram")

        await dispatcher.emit_agent_message(
            context,
            "result",
            "Done.\n---\n[继续] | [提交PR]",
        )

        self.assertEqual(len(controller.im_client.sent_button_messages), 1)
        keyboard = controller.im_client.sent_button_messages[0][3]
        self.assertEqual([[button.text for button in row] for row in keyboard.buttons], [["继续"], ["提交PR"]])

    async def test_discord_long_result_splits_into_multiple_messages_without_markdown_attachment(self):
        controller = _StubController("discord")
        dispatcher = ConsolidatedMessageDispatcher(controller)
        context = MessageContext(user_id="U1", channel_id="C1", platform="discord")
        long_text = " ".join(["Alpha"] * 320) + "\n\n" + " ".join(["Beta"] * 120)

        message_id = await dispatcher.emit_agent_message(context, "result", long_text)

        self.assertEqual(message_id, "msg-1")
        self.assertGreater(len(controller.im_client.sent_messages), 1)
        self.assertEqual(
            "".join(text for _, text, _ in controller.im_client.sent_messages),
            long_text,
        )
        self.assertEqual(controller.im_client.uploaded_markdowns, [])


if __name__ == "__main__":
    unittest.main()
