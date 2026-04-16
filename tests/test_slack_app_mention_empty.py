import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from config.v2_config import SlackConfig


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

    if "slack_sdk" not in sys.modules:
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

        sys.modules["slack_sdk"] = slack_sdk
        sys.modules["slack_sdk.web"] = web_mod
        sys.modules["slack_sdk.web.async_client"] = web_async_mod
        sys.modules["slack_sdk.socket_mode"] = socket_mode_mod
        sys.modules["slack_sdk.socket_mode.aiohttp"] = socket_mode_aiohttp_mod
        sys.modules["slack_sdk.socket_mode.request"] = socket_mode_request_mod
        sys.modules["slack_sdk.socket_mode.response"] = socket_mode_response_mod
        sys.modules["slack_sdk.errors"] = errors_mod


def _load_local_slack_bot():
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    sys.modules.pop("modules.im.slack", None)
    spec = importlib.util.spec_from_file_location("modules.im.slack", repo_root / "modules" / "im" / "slack.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["modules.im.slack"] = module
    spec.loader.exec_module(module)
    return module.SlackBot


_install_slack_stubs()
SlackBot = _load_local_slack_bot()


class SlackAppMentionEmptyTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_app_mention_does_not_activate_or_dispatch(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        received = {"called": False}

        async def _on_message(_context, _text):
            received["called"] = True

        slack.register_callbacks(on_message=_on_message)
        slack.settings_manager = object()
        slack.sessions = SimpleNamespace(mark_thread_active=Mock())
        slack._get_bot_user_id = AsyncMock(return_value="U_BOT")
        slack._extract_shared_message_content = AsyncMock(return_value=None)

        payload = {
            "event_id": "evt-app-mention-empty",
            "team_id": "T1",
            "authorizations": [{"user_id": "U_BOT"}],
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U123",
                "text": "<@U_BOT>",
                "ts": "1710000000.000700",
            },
        }

        await slack._handle_event(payload)

        self.assertFalse(received["called"])
        slack.sessions.mark_thread_active.assert_not_called()


class SlackFileAttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_file_attachments_preserves_file_id_when_url_missing(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))

        attachments = slack._extract_file_attachments([{"id": "F123", "mimetype": "application/pdf"}])

        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].name, "F123")
        self.assertIsNone(attachments[0].url)
        self.assertEqual(attachments[0].__dict__["slack_file_id"], "F123")

    async def test_resolve_downloadable_file_info_hydrates_thin_file_event(self):
        slack = SlackBot(SlackConfig(bot_token="xoxb-test"))
        slack.web_client = SimpleNamespace(
            files_info=AsyncMock(
                return_value={
                    "file": {
                        "id": "F123",
                        "name": "report.pdf",
                        "url_private_download": "https://files.slack.test/report.pdf",
                    }
                }
            )
        )

        resolved = await slack._resolve_downloadable_file_info({"slack_file_id": "F123", "name": "F123"})

        self.assertEqual(resolved["name"], "report.pdf")
        self.assertEqual(resolved["url_private_download"], "https://files.slack.test/report.pdf")
        slack.web_client.files_info.assert_awaited_once_with(file="F123")


if __name__ == "__main__":
    unittest.main()
