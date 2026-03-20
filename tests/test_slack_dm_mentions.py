import unittest
import sys
import types

from config.v2_config import SlackConfig


def _install_slack_stubs() -> None:
    if "aiohttp" not in sys.modules:
        sys.modules["aiohttp"] = types.ModuleType("aiohttp")

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

    class _SocketModeClient:
        def __init__(self, *args, **kwargs):
            pass

    class _SocketModeRequest:
        pass

    class _SocketModeResponse:
        def __init__(self, *args, **kwargs):
            pass

    class _SlackApiError(Exception):
        pass

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
        agents_mod = types.ModuleType("modules.agents")
        opencode_mod = types.ModuleType("modules.agents.opencode")
        utils_mod = types.ModuleType("modules.agents.opencode.utils")

        def _empty_list(*args, **kwargs):
            return []

        def _none(*args, **kwargs):
            return None

        utils_mod.build_claude_reasoning_options = _empty_list
        utils_mod.build_opencode_model_option_items = _empty_list
        utils_mod.build_codex_reasoning_options = _empty_list
        utils_mod.build_reasoning_effort_options = _empty_list
        utils_mod.resolve_opencode_allowed_providers = _none
        utils_mod.resolve_opencode_provider_preferences = _none

        sys.modules["modules.agents"] = agents_mod
        sys.modules["modules.agents.opencode"] = opencode_mod
        sys.modules["modules.agents.opencode.utils"] = utils_mod


_install_slack_stubs()

from modules.im.slack import SlackBot


class SlackDmMentionTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
