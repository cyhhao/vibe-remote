import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.agent_auth_service import AgentAuthService
from modules.im import MessageContext


class _ScenarioIMClient:
    def __init__(self):
        self.events = []

    async def send_message(self, context, text, parse_mode=None):
        self.events.append(("message", text))
        return f"msg-{len(self.events)}"

    async def send_message_with_buttons(self, context, text, keyboard, parse_mode=None):
        self.events.append(("buttons", text, keyboard))
        return f"btn-{len(self.events)}"

    def rendered_texts(self):
        return [event[1] for event in self.events]


class _ScenarioController:
    def __init__(self):
        self.config = SimpleNamespace(
            platform="slack",
            language="en",
            agents=SimpleNamespace(
                codex=SimpleNamespace(cli_path="codex"),
                claude=SimpleNamespace(cli_path="claude"),
                opencode=SimpleNamespace(cli_path="opencode"),
            ),
        )
        self.im_client = _ScenarioIMClient()
        self.agent_service = SimpleNamespace(agents={})
        self.sessions = SimpleNamespace(get_agent_session_id=lambda *args, **kwargs: None)
        self.session_handler = SimpleNamespace(
            get_session_info=lambda context: ("base-1", "/tmp/workdir", "base-1:/tmp/workdir"),
            get_working_path=lambda context: "/tmp/workdir",
        )

    def get_im_client_for_context(self, context):
        return self.im_client

    def _get_settings_key(self, context):
        return context.channel_id

    def _get_lang(self):
        return "en"

    def resolve_agent_for_context(self, context):
        return "codex"

    def get_opencode_overrides(self, context):
        return (None, None, None)


class _FakeProcess:
    def __init__(self):
        self.returncode = None
        self.stdout = SimpleNamespace(readline=AsyncMock(return_value=b""))
        self._done = asyncio.Event()

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def finish(self, returncode=0):
        self.returncode = returncode
        self._done.set()

    def terminate(self):
        self.finish(-15)

    def kill(self):
        self.finish(-9)


class _SetupScenarioHarness:
    def __init__(self):
        self.controller = _ScenarioController()
        self.service = AgentAuthService(self.controller)
        self.context = MessageContext(user_id="U1", channel_id="C1")

    def flow(self, backend: str):
        return self.service._flows[f"C1:{backend}"]

    def rendered_texts(self):
        return self.controller.im_client.rendered_texts()


class AgentAuthSetupScenarioTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_device_auth_scenario_reaches_terminal_success(self):
        harness = _SetupScenarioHarness()
        fake_process = _FakeProcess()
        harness.service._start_codex_process = AsyncMock(return_value=fake_process)
        harness.service._read_codex_output = AsyncMock(return_value=None)
        harness.service._verify_login = AsyncMock(return_value=(True, "Logged in using ChatGPT"))
        harness.service._refresh_backend_runtime = AsyncMock()

        await harness.service.start_setup(harness.context, backend="codex", force_reset=True)

        flow = harness.flow("codex")
        self.assertFalse(flow.waiter_task.done())

        await harness.service._handle_process_text(
            harness.context,
            "codex",
            "Open this URL to authenticate: https://auth.openai.com/codex/device",
        )
        await harness.service._handle_process_text(
            harness.context,
            "codex",
            "Then enter this code: T74L-XU61D",
        )

        fake_process.finish(0)
        await flow.waiter_task

        harness.service._refresh_backend_runtime.assert_awaited_once_with("codex")
        rendered = harness.rendered_texts()
        self.assertIn("starting codex", rendered[0].lower())
        self.assertIn("https://auth.openai.com/codex/device", rendered[1])
        self.assertIn("T74L-XU61D", rendered[1])
        self.assertIn("codex login is active again", rendered[-1].lower())
        self.assertNotIn("C1:codex", harness.service._flows)

    async def test_claude_manual_callback_scenario_accepts_plain_reply_and_completes(self):
        harness = _SetupScenarioHarness()
        fake_client = object()
        completion_released = asyncio.Event()
        callback_payloads = []

        harness.service._start_claude_control_flow = AsyncMock(
            return_value=(fake_client, "https://platform.claude.com/oauth/code/callback")
        )

        async def fake_control_request(client, request, timeout=900.0):
            self.assertIs(client, fake_client)
            if request["subtype"] == "claude_oauth_wait_for_completion":
                await completion_released.wait()
                return {}
            raise AssertionError(f"unexpected control request: {request}")

        async def fake_send_callback(client, authorization_code, state):
            self.assertIs(client, fake_client)
            callback_payloads.append((authorization_code, state))
            completion_released.set()

        harness.service._send_claude_control_request = AsyncMock(side_effect=fake_control_request)
        harness.service._send_claude_callback = AsyncMock(side_effect=fake_send_callback)
        harness.service._verify_login = AsyncMock(return_value=(True, '{"loggedIn": true}'))
        harness.service._refresh_backend_runtime = AsyncMock()
        harness.service._disconnect_claude_client = AsyncMock()

        await harness.service.start_setup(
            harness.context,
            backend="claude",
            force_reset=True,
            claude_login_method="console",
        )

        flow = harness.flow("claude")
        self.assertFalse(flow.waiter_task.done())

        consumed = await harness.service.maybe_consume_setup_reply(harness.context, "auth-code#oauth-state")
        self.assertTrue(consumed)

        await flow.waiter_task

        self.assertEqual(callback_payloads, [("auth-code", "oauth-state")])
        harness.service._refresh_backend_runtime.assert_awaited_once_with("claude")
        harness.service._disconnect_claude_client.assert_awaited_once_with(fake_client)
        rendered = harness.rendered_texts()
        self.assertIn("starting claude", rendered[0].lower())
        self.assertIn("https://platform.claude.com/oauth/code/callback", rendered[1])
        self.assertIn("submitted", rendered[2].lower())
        self.assertIn("claude login is active again", rendered[-1].lower())
        self.assertNotIn("C1:claude", harness.service._flows)

    async def test_opencode_direct_key_scenario_installs_key_and_refreshes_runtime(self):
        harness = _SetupScenarioHarness()
        harness.service._resolve_opencode_provider = AsyncMock(return_value="opencode")
        harness.service._install_opencode_api_key = AsyncMock()
        harness.service._refresh_backend_runtime = AsyncMock()
        harness.service._clear_backend_sessions_for_context = AsyncMock()

        await harness.service.start_setup(harness.context, backend="opencode", force_reset=True)

        flow = harness.flow("opencode")
        self.assertTrue(flow.awaiting_code)
        self.assertEqual(flow.url, "https://opencode.ai/auth")

        consumed = await harness.service.maybe_consume_setup_reply(
            harness.context,
            "oc_live_Abcdef1234567890",
        )
        self.assertTrue(consumed)

        harness.service._install_opencode_api_key.assert_awaited_once_with("opencode", "oc_live_Abcdef1234567890")
        harness.service._refresh_backend_runtime.assert_awaited_once_with("opencode")
        harness.service._clear_backend_sessions_for_context.assert_awaited_once_with("opencode", harness.context)
        rendered = harness.rendered_texts()
        self.assertIn("starting opencode", rendered[0].lower())
        self.assertIn("https://opencode.ai/auth", rendered[1])
        self.assertIn("opencode login is active again", rendered[-1].lower())
        self.assertNotIn("C1:opencode", harness.service._flows)


if __name__ == "__main__":
    unittest.main()
