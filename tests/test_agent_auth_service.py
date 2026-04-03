import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.agent_auth_service import (
    AgentAuthFlow,
    AgentAuthService,
    classify_auth_error,
    verify_opencode_auth_list_output,
)
from modules.im import MessageContext


class _StubIMClient:
    def __init__(self):
        self.sent_messages = []
        self.sent_button_messages = []

    async def send_message(self, context, text, parse_mode=None):
        self.sent_messages.append((context.channel_id, text))
        return "msg-1"

    async def send_message_with_buttons(self, context, text, keyboard, parse_mode=None):
        self.sent_button_messages.append((context.channel_id, text, keyboard))
        return "btn-1"


class _StubController:
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
        self.im_client = _StubIMClient()
        self.agent_service = SimpleNamespace(agents={})
        self.resolve_agent_for_context = AsyncMock(return_value="codex")

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


class AgentAuthServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_setup_command_submits_code(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        service.submit_code = AsyncMock()
        context = MessageContext(user_id="U1", channel_id="C1")

        await service.handle_setup_command(context, "code 123456")

        service.submit_code.assert_awaited_once_with(context, "123456")

    async def test_handle_setup_command_starts_explicit_backend(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        service.start_setup = AsyncMock()
        context = MessageContext(user_id="U1", channel_id="C1")

        await service.handle_setup_command(context, "claude")

        service.start_setup.assert_awaited_once_with(context, backend="claude", force_reset=True)

    async def test_handle_setup_command_supports_opencode_alias(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        service.start_setup = AsyncMock()
        context = MessageContext(user_id="U1", channel_id="C1")

        await service.handle_setup_command(context, "oc")

        service.start_setup.assert_awaited_once_with(context, backend="opencode", force_reset=True)

    async def test_maybe_emit_auth_recovery_message_sends_reset_button(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")

        handled = await service.maybe_emit_auth_recovery_message(
            context,
            "codex",
            "❌ Codex error: 401 Unauthorized",
        )

        self.assertTrue(handled)
        self.assertEqual(len(controller.im_client.sent_button_messages), 1)
        _, text, keyboard = controller.im_client.sent_button_messages[0]
        self.assertIn("401 Unauthorized", text)
        self.assertEqual(keyboard.buttons[0][0].callback_data, "auth_setup:codex")

    async def test_handle_process_text_emits_codex_link_once_url_and_code_exist(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-1",
            backend="codex",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
        )
        service._flows[flow.flow_key] = flow

        await service._handle_process_text(context, "codex", "https://auth.openai.com/codex/device")
        await service._handle_process_text(context, "codex", "T74L-XU61D")

        self.assertEqual(len(controller.im_client.sent_messages), 1)
        _, text = controller.im_client.sent_messages[0]
        self.assertIn("T74L-XU61D", text)
        self.assertIn("https://auth.openai.com/codex/device", text)

    async def test_handle_process_text_marks_claude_flow_awaiting_code(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-2",
            backend="claude",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            pty_master_fd=10,
        )
        service._flows[flow.flow_key] = flow

        await service._handle_process_text(
            context,
            "claude",
            "Paste code here if prompted >",
        )

        self.assertTrue(flow.awaiting_code)
        self.assertEqual(len(controller.im_client.sent_messages), 1)
        self.assertIn("/setup code <value>", controller.im_client.sent_messages[0][1])

    async def test_handle_process_text_marks_opencode_flow_awaiting_api_key(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-3",
            backend="opencode",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            pty_master_fd=10,
            provider="opencode",
        )
        service._flows[flow.flow_key] = flow

        await service._handle_process_text(
            context,
            "opencode",
            "Create an api key at https://opencode.ai/auth",
        )
        await service._handle_process_text(
            context,
            "opencode",
            "E\nn\nt\ne\nr\ny\no\nu\nr\nA\nP\nI\nk\ne\ny",
        )

        self.assertTrue(flow.awaiting_code)
        self.assertEqual(len(controller.im_client.sent_messages), 2)
        self.assertIn("https://opencode.ai/auth", controller.im_client.sent_messages[0][1])
        self.assertIn("/setup code <value>", controller.im_client.sent_messages[1][1])

    async def test_resolve_opencode_provider_prefers_override_model(self):
        controller = _StubController()
        controller.get_opencode_overrides = lambda context: ("build", "openai/gpt-5.4", None)
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")

        provider = await service._resolve_opencode_provider(context)

        self.assertEqual(provider, "openai")

    async def test_handle_process_text_emits_opencode_device_flow_for_openai(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-4",
            backend="opencode",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            pty_master_fd=10,
            provider="openai",
        )
        service._flows[flow.flow_key] = flow

        await service._handle_process_text(
            context,
            "opencode",
            "Go to: https://auth.openai.com/codex/device",
        )
        await service._handle_process_text(
            context,
            "opencode",
            "Enter code: TRVY-E7DCU",
        )

        self.assertEqual(len(controller.im_client.sent_messages), 1)
        message = controller.im_client.sent_messages[0][1]
        self.assertIn("https://auth.openai.com/codex/device", message)
        self.assertIn("TRVY-E7DCU", message)

    async def test_submit_code_prefers_same_user_flow_waiting_for_code(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        codex_flow = AgentAuthFlow(
            flow_id="flow-codex",
            backend="codex",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
        )
        opencode_flow = AgentAuthFlow(
            flow_id="flow-opencode",
            backend="opencode",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            pty_master_fd=17,
            awaiting_code=True,
            provider="opencode",
        )
        service._flows[codex_flow.flow_key] = codex_flow
        service._flows[opencode_flow.flow_key] = opencode_flow

        with patch("core.agent_auth_service.os.write") as mock_write:
            await service.submit_code(context, "secret-value")

        mock_write.assert_called_once_with(17, b"secret-value\n")
        self.assertFalse(opencode_flow.awaiting_code)
        self.assertIn("opencode", controller.im_client.sent_messages[0][1].lower())

    async def test_drop_flow_preserves_replacement_flow_with_same_key(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task

        existing_flow = AgentAuthFlow(
            flow_id="flow-old",
            backend="claude",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            pty_master_fd=10,
        )
        replacement_flow = AgentAuthFlow(
            flow_id="flow-new",
            backend="claude",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            pty_master_fd=11,
        )
        service._flows[existing_flow.flow_key] = replacement_flow
        service._flows_by_id[existing_flow.flow_id] = existing_flow
        service._flows_by_id[replacement_flow.flow_id] = replacement_flow

        service._drop_flow(existing_flow)

        self.assertIs(service._flows[existing_flow.flow_key], replacement_flow)
        self.assertNotIn(existing_flow.flow_id, service._flows_by_id)
        self.assertIs(service._flows_by_id[replacement_flow.flow_id], replacement_flow)

    async def test_start_claude_process_closes_master_fd_on_spawn_failure(self):
        controller = _StubController()
        service = AgentAuthService(controller)

        with (
            patch("core.agent_auth_service.os.openpty", return_value=(101, 202)),
            patch("core.agent_auth_service.os.close") as mock_close,
            patch("core.agent_auth_service.asyncio.create_subprocess_exec", side_effect=RuntimeError("boom")),
        ):
            with self.assertRaises(RuntimeError):
                await service._start_claude_process(force_reset=False)

        mock_close.assert_any_call(101)
        mock_close.assert_any_call(202)

    async def test_start_opencode_process_closes_master_fd_on_spawn_failure(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        service._resolve_opencode_provider = AsyncMock(return_value="openai")

        with (
            patch("core.agent_auth_service.os.openpty", return_value=(303, 404)),
            patch("core.agent_auth_service.os.close") as mock_close,
            patch("core.agent_auth_service.asyncio.create_subprocess_exec", side_effect=RuntimeError("boom")),
        ):
            with self.assertRaises(RuntimeError):
                await service._start_opencode_process(context, force_reset=False)

        mock_close.assert_any_call(303)
        mock_close.assert_any_call(404)

    async def test_read_pty_output_exits_after_process_finishes_without_output(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        process = SimpleNamespace(returncode=None)
        master_fd, slave_fd = os.openpty()

        async def finish_process():
            await asyncio.sleep(0.05)
            process.returncode = 0
            os.close(slave_fd)

        finisher = asyncio.create_task(finish_process())
        try:
            await asyncio.wait_for(service._read_pty_output(process, master_fd, context, "claude"), timeout=0.5)
        finally:
            await finisher


class ClassifyAuthErrorTests(unittest.TestCase):
    def test_codex_401_requires_reset(self):
        self.assertTrue(classify_auth_error("codex", "unexpected status 401 Unauthorized"))

    def test_non_auth_error_is_ignored(self):
        self.assertFalse(classify_auth_error("codex", "temporary network timeout"))

    def test_opencode_credential_error_requires_reset(self):
        self.assertTrue(classify_auth_error("opencode", "OpenCode error: missing provider credential"))


class VerifyOpenCodeAuthListOutputTests(unittest.TestCase):
    def test_target_provider_must_exist_in_output(self):
        text = """
        ┌ Credentials ~/.local/share/opencode/auth.json
        │ anthropic 1 credential
        └ 1 credentials
        """

        self.assertFalse(verify_opencode_auth_list_output(text, "openai"))

    def test_target_provider_uses_its_own_credential_count(self):
        text = """
        ┌ Credentials ~/.local/share/opencode/auth.json
        │ openai 0 credentials
        │ anthropic 1 credential
        └ 1 credentials
        """

        self.assertFalse(verify_opencode_auth_list_output(text, "openai"))
        self.assertTrue(verify_opencode_auth_list_output(text, "anthropic"))

    def test_provider_does_not_match_header_path(self):
        text = """
        ┌ Credentials ~/.local/share/opencode/auth.json
        │ anthropic 1 credential
        └ 1 credentials
        """

        self.assertFalse(verify_opencode_auth_list_output(text, "opencode"))


if __name__ == "__main__":
    unittest.main()
