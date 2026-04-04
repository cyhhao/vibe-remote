import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.scenario_harness.auth_setup import AuthSetupScenarioHarness, FakeProcess


class AgentAuthSetupScenarioTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_device_auth_scenario_reaches_terminal_success(self):
        harness = AuthSetupScenarioHarness()
        fake_process = FakeProcess()
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
        harness = AuthSetupScenarioHarness()
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
        harness = AuthSetupScenarioHarness()
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
