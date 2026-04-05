import importlib.util
import unittest
import sys
import types
from pathlib import Path

from config.v2_config import SlackConfig
from modules.im.base import InlineButton, InlineKeyboard, MessageContext


def _install_slack_stubs() -> None:
    if "aiohttp" not in sys.modules:
        aiohttp_mod = types.ModuleType("aiohttp")

        class _ClientWebSocketResponse:
            closed = False

        class _ClientSession:
            async def close(self):
                return None

        class _ClientTimeout:
            def __init__(self, *args, **kwargs):
                pass

        aiohttp_mod.ClientWebSocketResponse = _ClientWebSocketResponse
        aiohttp_mod.ClientSession = _ClientSession
        aiohttp_mod.ClientTimeout = _ClientTimeout
        sys.modules["aiohttp"] = aiohttp_mod

    if "markdown_to_mrkdwn" not in sys.modules:
        markdown_mod = types.ModuleType("markdown_to_mrkdwn")

        class _SlackMarkdownConverter:
            def convert(self, text):
                return text

        markdown_mod.SlackMarkdownConverter = _SlackMarkdownConverter
        sys.modules["markdown_to_mrkdwn"] = markdown_mod

    if "slack_sdk" in sys.modules:
        pass

    slack_sdk = types.ModuleType("slack_sdk")
    web_mod = types.ModuleType("slack_sdk.web")
    web_async_mod = types.ModuleType("slack_sdk.web.async_client")
    socket_mode_mod = types.ModuleType("slack_sdk.socket_mode")
    socket_mode_aiohttp_mod = types.ModuleType("slack_sdk.socket_mode.aiohttp")
    socket_mode_request_mod = types.ModuleType("slack_sdk.socket_mode.request")
    socket_mode_response_mod = types.ModuleType("slack_sdk.socket_mode.response")
    errors_mod = types.ModuleType("slack_sdk.errors")

    class _AsyncWebClient:
        def __init__(self, *args, **kwargs):
            pass

        async def auth_test(self):
            return {"user_id": "U_BOT"}

    class _SocketModeClient:
        def __init__(self, *args, **kwargs):
            pass

    class _SocketModeRequest:
        pass

    class _SocketModeResponse:
        def __init__(self, *args, **kwargs):
            pass

    class _SlackApiError(Exception):
        def __init__(self, message="", response=None):
            super().__init__(message)
            self.response = response

    web_async_mod.AsyncWebClient = _AsyncWebClient
    socket_mode_aiohttp_mod.SocketModeClient = _SocketModeClient
    socket_mode_request_mod.SocketModeRequest = _SocketModeRequest
    socket_mode_response_mod.SocketModeResponse = _SocketModeResponse
    errors_mod.SlackApiError = _SlackApiError

    if "slack_sdk" not in sys.modules:
        sys.modules["slack_sdk"] = slack_sdk
        sys.modules["slack_sdk.web"] = web_mod
        sys.modules["slack_sdk.web.async_client"] = web_async_mod
        sys.modules["slack_sdk.socket_mode"] = socket_mode_mod
        sys.modules["slack_sdk.socket_mode.aiohttp"] = socket_mode_aiohttp_mod
        sys.modules["slack_sdk.socket_mode.request"] = socket_mode_request_mod
        sys.modules["slack_sdk.socket_mode.response"] = socket_mode_response_mod
        sys.modules["slack_sdk.errors"] = errors_mod

    if "modules.agents.opencode.utils" not in sys.modules:
        repo_root = Path(__file__).resolve().parents[1]
        agents_mod = types.ModuleType("modules.agents")
        agents_mod.__path__ = [str(repo_root / "modules" / "agents")]
        opencode_mod = types.ModuleType("modules.agents.opencode")
        opencode_mod.__path__ = [str(repo_root / "modules" / "agents" / "opencode")]

        sys.modules["modules.agents"] = agents_mod
        sys.modules["modules.agents.opencode"] = opencode_mod

        spec = importlib.util.spec_from_file_location(
            "modules.agents.opencode.utils",
            repo_root / "modules" / "agents" / "opencode" / "utils.py",
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["modules.agents.opencode.utils"] = module
        spec.loader.exec_module(module)


_install_slack_stubs()

from modules.im.slack import SlackBot


class SlackDmMentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_message_recovers_dm_channel_after_channel_not_found(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        sent_channels = []
        sent_thread_ts = []

        class _WebClient:
            def __init__(self):
                self.fail_once = True

            async def chat_postMessage(self, **kwargs):
                sent_channels.append(kwargs["channel"])
                sent_thread_ts.append(kwargs.get("thread_ts"))
                if self.fail_once:
                    self.fail_once = False
                    raise sys.modules["slack_sdk.errors"].SlackApiError(
                        "channel missing",
                        response={"error": "channel_not_found"},
                    )
                return {"ts": "1710000000.000001"}

            async def conversations_open(self, users):
                return {"ok": True, "channel": {"id": "D999"}}

        slack.web_client = _WebClient()
        context = MessageContext(
            user_id="U123",
            channel_id="D123",
            thread_id="1710000000.000100",
            platform_specific={"is_dm": True},
        )

        message_ts = await slack.send_message(context, "hello", parse_mode="markdown")

        self.assertEqual(message_ts, "1710000000.000001")
        self.assertEqual(sent_channels, ["D123", "D999"])
        self.assertEqual(sent_thread_ts, ["1710000000.000100", None])
        self.assertEqual(context.channel_id, "D999")
        self.assertIsNone(context.thread_id)

    async def test_send_message_with_buttons_recovers_dm_channel_after_channel_not_found(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        sent_channels = []
        sent_thread_ts = []

        class _WebClient:
            def __init__(self):
                self.fail_once = True

            async def chat_postMessage(self, **kwargs):
                sent_channels.append(kwargs["channel"])
                sent_thread_ts.append(kwargs.get("thread_ts"))
                if self.fail_once:
                    self.fail_once = False
                    raise sys.modules["slack_sdk.errors"].SlackApiError(
                        "channel missing",
                        response={"error": "channel_not_found"},
                    )
                return {"ts": "1710000000.000002"}

            async def conversations_open(self, users):
                return {"ok": True, "channel": {"id": "D999"}}

        slack.web_client = _WebClient()
        context = MessageContext(
            user_id="U123",
            channel_id="D123",
            thread_id="1710000000.000100",
            platform_specific={"is_dm": True},
        )
        keyboard = InlineKeyboard(buttons=[[InlineButton(text="One", callback_data="choose:1")]])

        message_ts = await slack.send_message_with_buttons(context, "hello", keyboard, parse_mode="markdown")

        self.assertEqual(message_ts, "1710000000.000002")
        self.assertEqual(sent_channels, ["D123", "D999"])
        self.assertEqual(sent_thread_ts, ["1710000000.000100", None])
        self.assertEqual(context.channel_id, "D999")
        self.assertIsNone(context.thread_id)

    async def test_send_message_with_buttons_splits_long_text_before_button_block(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        sent_payloads = []

        class _WebClient:
            async def chat_postMessage(self, **kwargs):
                sent_payloads.append(kwargs)
                return {"ts": f"1710000000.00000{len(sent_payloads)}"}

        slack.web_client = _WebClient()
        context = MessageContext(user_id="U123", channel_id="C123")
        keyboard = InlineKeyboard(buttons=[[InlineButton(text="One", callback_data="choose:1")]])
        text = "\n\n".join([f"Paragraph {index} {'x' * 120}" for index in range(30)])

        message_ts = await slack.send_message_with_buttons(context, text, keyboard, parse_mode="markdown")

        self.assertEqual(message_ts, "1710000000.000002")
        self.assertEqual(len(sent_payloads), 2)
        self.assertEqual(sent_payloads[0]["text"] + sent_payloads[1]["text"], text)
        self.assertTrue(all(len(payload["text"]) <= 3000 for payload in sent_payloads))
        self.assertFalse(any(block["type"] == "actions" for block in sent_payloads[0].get("blocks", [])))
        self.assertEqual(sent_payloads[1]["blocks"][0]["type"], "section")
        self.assertEqual(sent_payloads[1]["blocks"][1]["type"], "actions")

    async def test_remove_inline_keyboard_uses_visible_chunk_for_long_text(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        updates = []

        class _WebClient:
            async def chat_update(self, **kwargs):
                updates.append(kwargs)
                return {"ok": True}

        slack.web_client = _WebClient()
        context = MessageContext(user_id="U123", channel_id="C123")
        text = "\n\n".join([f"Paragraph {index} {'x' * 120}" for index in range(30)])

        ok = await slack.remove_inline_keyboard(context, "1710000000.000002", text=text, parse_mode="markdown")

        self.assertTrue(ok)
        self.assertEqual(len(updates), 1)
        self.assertLessEqual(len(updates[0]["text"]), 3000)
        self.assertEqual(updates[0]["text"], slack._get_visible_text(text))
        self.assertEqual(updates[0]["blocks"][0]["type"], "section")

    def test_split_text_keeps_boundary_chunk_within_limit(self):
        chunks = SlackBot._split_text("a" * 3000 + " " + "b", 3000)

        self.assertEqual(chunks, ["a" * 3000, " b"])
        self.assertTrue(all(len(chunk) <= 3000 for chunk in chunks))

    async def test_get_user_info_prefers_normalized_profile_names(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))

        class _WebClient:
            async def users_info(self, user):
                return {
                    "user": {
                        "id": user,
                        "name": "cyh",
                        "real_name": "Alex Raw",
                        "profile": {
                            "display_name": "Alex Raw Display",
                            "display_name_normalized": "Alex",
                            "real_name_normalized": "Alex",
                            "email": "alex@example.com",
                        },
                    }
                }

        slack.web_client = _WebClient()

        user_info = await slack.get_user_info("U0E0FM3QT")

        self.assertEqual(user_info["display_name"], "Alex")
        self.assertEqual(user_info["real_name"], "Alex")
        self.assertEqual(user_info["name"], "cyh")

    async def test_dm_mention_only_falls_through_as_empty_message(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        received = {}

        async def _on_message(context, text):
            received["channel_id"] = context.channel_id
            received["thread_id"] = context.thread_id
            received["text"] = text

        slack.register_callbacks(on_message=_on_message)

        payload = {
            "event_id": "evt-dm-mention-only",
            "team_id": "T1",
            "authorizations": [{"user_id": "U_BOT"}],
            "event": {
                "type": "message",
                "channel": "D123",
                "user": "U123",
                "text": "<@U_BOT>",
                "ts": "1710000000.000100",
            },
        }

        await slack._handle_event(payload)

        self.assertEqual(
            received,
            {
                "channel_id": "D123",
                "thread_id": "1710000000.000100",
                "text": "",
            },
        )

    async def test_dm_mention_with_text_strips_mention(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        received = {}

        async def _on_message(context, text):
            received["text"] = text

        slack.register_callbacks(on_message=_on_message)

        payload = {
            "event_id": "evt-dm-mention-text",
            "team_id": "T1",
            "authorizations": [{"user_id": "U_BOT"}],
            "event": {
                "type": "message",
                "channel": "D123",
                "user": "U123",
                "text": "<@U_BOT> hello",
                "ts": "1710000000.000200",
            },
        }

        await slack._handle_event(payload)

        self.assertEqual(received, {"text": "hello"})

    async def test_channel_mention_still_waits_for_app_mention(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        received = {"called": False}

        async def _on_message(_context, _text):
            received["called"] = True

        slack.register_callbacks(on_message=_on_message)

        payload = {
            "event_id": "evt-channel-mention",
            "team_id": "T1",
            "authorizations": [{"user_id": "U_BOT"}],
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": "<@U_BOT>",
                "ts": "1710000000.000300",
            },
        }

        await slack._handle_event(payload)

        self.assertFalse(received["called"])

    async def test_dm_preserves_non_bot_mentions(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        received = {}

        async def _on_message(_context, text):
            received["text"] = text

        slack.register_callbacks(on_message=_on_message)

        payload = {
            "event_id": "evt-dm-mention-preserve-other",
            "team_id": "T1",
            "authorizations": [{"user_id": "U_BOT"}],
            "event": {
                "type": "message",
                "channel": "D123",
                "user": "U123",
                "text": "<@U_BOT> summarize what <@U_OTHER> said",
                "ts": "1710000000.000400",
            },
        }

        await slack._handle_event(payload)

        self.assertEqual(received, {"text": "summarize what <@U_OTHER> said"})

    async def test_channel_message_with_other_mentions_is_not_skipped(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        received = {}

        async def _on_message(_context, text):
            received["text"] = text

        slack.register_callbacks(on_message=_on_message)

        payload = {
            "event_id": "evt-channel-other-mention",
            "team_id": "T1",
            "authorizations": [{"user_id": "U_BOT"}],
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": "please ask <@U_OTHER> to check",
                "ts": "1710000000.000500",
            },
        }

        await slack._handle_event(payload)

        self.assertEqual(received, {"text": "please ask <@U_OTHER> to check"})

    async def test_channel_message_with_bot_mention_mid_text_is_skipped(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        received = {"called": False}

        async def _on_message(_context, _text):
            received["called"] = True

        slack.register_callbacks(on_message=_on_message)

        payload = {
            "event_id": "evt-channel-bot-mention-middle",
            "team_id": "T1",
            "authorizations": [{"user_id": "U_BOT"}],
            "event": {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": "hello <@U_BOT> please help",
                "ts": "1710000000.000550",
            },
        }

        await slack._handle_event(payload)

        self.assertFalse(received["called"])

    async def test_app_mention_preserves_non_bot_mentions(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        received = {}

        async def _on_message(_context, text):
            received["text"] = text

        slack.register_callbacks(on_message=_on_message)

        payload = {
            "event_id": "evt-app-mention-preserve-other",
            "team_id": "T1",
            "authorizations": [{"user_id": "U_BOT"}],
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U123",
                "text": "<@U_BOT> summarize what <@U_OTHER> said",
                "ts": "1710000000.000600",
            },
        }

        await slack._handle_event(payload)

        self.assertEqual(received, {"text": "summarize what <@U_OTHER> said"})


if __name__ == "__main__":
    unittest.main()
