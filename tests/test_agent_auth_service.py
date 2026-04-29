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
from modules.claude_sdk_compat import CLAUDE_SDK_MAX_BUFFER_SIZE
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
        self.sessions = SimpleNamespace(get_agent_session_id=lambda *args, **kwargs: None)
        self.session_handler = SimpleNamespace(
            get_session_info=lambda context: ("base-1", "/tmp/workdir", "base-1:/tmp/workdir")
        )
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

        service.start_setup.assert_awaited_once_with(
            context,
            backend="claude",
            force_reset=True,
            claude_login_method=None,
        )

    async def test_handle_setup_command_supports_opencode_alias(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        service.start_setup = AsyncMock()
        context = MessageContext(user_id="U1", channel_id="C1")

        await service.handle_setup_command(context, "oc")

        service.start_setup.assert_awaited_once_with(
            context,
            backend="opencode",
            force_reset=True,
            claude_login_method=None,
        )

    async def test_handle_setup_command_supports_claude_console_login_option(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        service.start_setup = AsyncMock()
        context = MessageContext(user_id="U1", channel_id="C1")

        await service.handle_setup_command(context, "claude console")

        service.start_setup.assert_awaited_once_with(
            context,
            backend="claude",
            force_reset=True,
            claude_login_method="console",
        )

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

    async def test_start_setup_starts_codex_waiter(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        process = SimpleNamespace(stdout=object(), returncode=None)
        service._start_codex_process = AsyncMock(return_value=process)
        service._read_codex_output = AsyncMock()
        service._wait_for_completion = AsyncMock()

        await service.start_setup(context, backend="codex", force_reset=True)
        await asyncio.sleep(0)

        service._start_codex_process.assert_awaited_once_with(force_reset=True)
        service._read_codex_output.assert_awaited_once_with(process, context, "codex")
        service._wait_for_completion.assert_awaited_once()

    async def test_start_setup_starts_claude_control_flow_and_emits_manual_url(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        mock_client = SimpleNamespace()
        service._start_claude_control_flow = AsyncMock(return_value=(mock_client, "https://platform.claude.com/oauth/code"))
        service._wait_for_claude_completion = AsyncMock()

        await service.start_setup(context, backend="claude", force_reset=True, claude_login_method="console")

        service._start_claude_control_flow.assert_awaited_once_with(
            context,
            force_reset=True,
            login_with_claude_ai=False,
        )
        self.assertEqual(len(controller.im_client.sent_messages), 2)
        self.assertIn("https://platform.claude.com/oauth/code", controller.im_client.sent_messages[1][1])
        flow = service._flows["C1:claude"]
        self.assertIs(flow.claude_client, mock_client)
        self.assertTrue(flow.login_prompt_sent)

    async def test_start_setup_prompts_for_claude_login_method_when_unspecified(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")

        await service.start_setup(context, backend="claude", force_reset=True, claude_login_method=None)

        self.assertEqual(len(controller.im_client.sent_button_messages), 1)
        _, text, keyboard = controller.im_client.sent_button_messages[0]
        self.assertIn("sign-in source", text.lower())
        self.assertIn("/setup claude claudeai", text)
        self.assertIn("/setup claude console", text)
        self.assertEqual(keyboard.buttons[0][0].callback_data, "auth_setup:claude:claudeai")
        self.assertEqual(keyboard.buttons[0][1].callback_data, "auth_setup:claude:console")

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
        self.assertIn("reply", controller.im_client.sent_messages[1][1].lower())

    async def test_start_setup_uses_direct_opencode_api_key_flow_for_opencode_provider(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        service._resolve_opencode_provider = AsyncMock(return_value="opencode")
        service._start_opencode_process = AsyncMock()

        await service.start_setup(context, backend="opencode", force_reset=True)

        service._start_opencode_process.assert_not_awaited()
        flow = service._flows["C1:opencode"]
        self.assertTrue(flow.awaiting_code)
        self.assertIsNone(flow.pty_master_fd)
        self.assertEqual(flow.url, "https://opencode.ai/auth")
        self.assertIn("reply", controller.im_client.sent_messages[1][1].lower())

    async def test_handle_process_text_reprompts_when_opencode_requests_api_key_again(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-opencode-retry",
            backend="opencode",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            pty_master_fd=12,
            awaiting_code=False,
            code_prompt_sent=True,
            provider="opencode",
        )
        service._flows[flow.flow_key] = flow

        await service._handle_process_text(
            context,
            "opencode",
            "E\nn\nt\ne\nr\ny\no\nu\nr\nA\nP\nI\nk\ne\ny",
        )

        self.assertTrue(flow.awaiting_code)
        self.assertEqual(len(controller.im_client.sent_messages), 1)
        self.assertIn("still waiting", controller.im_client.sent_messages[0][1].lower())

    async def test_resolve_opencode_provider_prefers_override_model(self):
        controller = _StubController()
        controller.get_opencode_overrides = lambda context: ("build", "openai/gpt-5.4", None)
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")

        provider = await service._resolve_opencode_provider(context)

        self.assertEqual(provider, "openai")

    async def test_resolve_opencode_provider_prefers_existing_session_runtime_provider(self):
        controller = _StubController()
        controller.sessions = SimpleNamespace(
            get_agent_session_id=lambda session_key, composite_key, agent_name: "ses-existing"
        )
        mock_server = SimpleNamespace(
            list_messages=AsyncMock(
                return_value=[
                    {"info": {"role": "assistant", "providerID": "openai", "modelID": "gpt-5.3-chat-latest"}}
                ]
            ),
            get_default_agent_from_config=lambda: "build",
            get_agent_model_from_config=lambda agent_name: None,
        )
        controller.agent_service = SimpleNamespace(
            agents={"opencode": SimpleNamespace(_get_server=AsyncMock(return_value=mock_server))}
        )
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")

        provider = await service._resolve_opencode_provider(context)

        self.assertEqual(provider, "openai")
        mock_server.list_messages.assert_awaited_once_with("ses-existing", "/tmp/workdir")

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
            provider="openrouter",
        )
        service._flows[codex_flow.flow_key] = codex_flow
        service._flows[opencode_flow.flow_key] = opencode_flow

        with patch("core.agent_auth_service.os.write") as mock_write:
            await service.submit_code(context, "secret-value")

        mock_write.assert_called_once_with(17, b"secret-value\n")
        self.assertFalse(opencode_flow.awaiting_code)
        self.assertIn("opencode", controller.im_client.sent_messages[0][1].lower())

    async def test_submit_code_installs_direct_opencode_api_key_without_pty(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-opencode-direct",
            backend="opencode",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=None,
            reader_task=done_task,
            waiter_task=done_task,
            provider="opencode",
            awaiting_code=True,
        )
        service._flows[flow.flow_key] = flow
        service._flows_by_id[flow.flow_id] = flow
        service._install_opencode_api_key = AsyncMock()
        service._refresh_backend_runtime = AsyncMock()
        service._clear_backend_sessions_for_context = AsyncMock()

        await service.submit_code(context, "sk-opencode-secret", backend_hint="opencode")

        service._install_opencode_api_key.assert_awaited_once_with("opencode", "sk-opencode-secret")
        service._refresh_backend_runtime.assert_awaited_once_with("opencode")
        service._clear_backend_sessions_for_context.assert_awaited_once_with("opencode", context)
        self.assertIn("active again", controller.im_client.sent_messages[0][1].lower())
        self.assertNotIn(flow.flow_key, service._flows)

    async def test_install_opencode_api_key_uses_server_auth_endpoint(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        order = []

        async def set_api_key_auth(provider, api_key):
            order.append(("set", provider, api_key))

        server = SimpleNamespace(set_api_key_auth=AsyncMock(side_effect=set_api_key_auth))
        controller.agent_service = SimpleNamespace(
            agents={"opencode": SimpleNamespace(_get_server=AsyncMock(return_value=server))}
        )

        cleanup_calls = []

        async def fake_to_thread(func, *args, **kwargs):
            cleanup_calls.append((func, args, kwargs))
            order.append(("cleanup", args[0]))
            return func(*args, **kwargs)

        with patch("core.agent_auth_service.asyncio.to_thread", side_effect=fake_to_thread):
            await service._install_opencode_api_key("opencode", "sk-opencode-secret")

        self.assertEqual(len(cleanup_calls), 1)
        _, args, kwargs = cleanup_calls[0]
        self.assertEqual(args, ("opencode",))
        self.assertEqual(kwargs, {"logger_instance": unittest.mock.ANY})
        server.set_api_key_auth.assert_awaited_once_with("opencode", "sk-opencode-secret")
        self.assertEqual(order, [("set", "opencode", "sk-opencode-secret"), ("cleanup", "opencode")])

    async def test_submit_code_allows_proactive_claude_code_submission(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        claude_flow = AgentAuthFlow(
            flow_id="flow-claude-code",
            backend="claude",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=None,
            reader_task=done_task,
            waiter_task=done_task,
            claude_client=SimpleNamespace(),
            login_prompt_sent=True,
        )
        service._flows[claude_flow.flow_key] = claude_flow

        service._send_claude_callback = AsyncMock()

        await service.submit_code(context, "auth-code#oauth-state")

        service._send_claude_callback.assert_awaited_once_with(claude_flow.claude_client, "auth-code", "oauth-state")
        self.assertIn("claude", controller.im_client.sent_messages[0][1].lower())

    async def test_submit_code_rejects_invalid_claude_callback_format(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-claude-invalid",
            backend="claude",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=None,
            reader_task=done_task,
            waiter_task=done_task,
            claude_client=SimpleNamespace(),
            login_prompt_sent=True,
        )
        service._flows[flow.flow_key] = flow
        service._send_claude_callback = AsyncMock()

        await service.submit_code(context, "12345678")

        service._send_claude_callback.assert_not_awaited()
        self.assertIn("authorizationCode#state", controller.im_client.sent_messages[0][1])

    async def test_maybe_consume_setup_reply_accepts_plain_claude_callback_value(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-claude-plain",
            backend="claude",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=None,
            reader_task=done_task,
            waiter_task=done_task,
            claude_client=SimpleNamespace(),
            login_prompt_sent=True,
        )
        service._flows[flow.flow_key] = flow
        service.submit_code = AsyncMock()

        consumed = await service.maybe_consume_setup_reply(context, "auth-code#oauth-state")

        self.assertTrue(consumed)
        service.submit_code.assert_awaited_once_with(context, "auth-code#oauth-state", backend_hint="claude")

    async def test_maybe_consume_setup_reply_prefers_claude_callback_over_opencode_waiting_key(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        claude_flow = AgentAuthFlow(
            flow_id="flow-claude-priority",
            backend="claude",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=None,
            reader_task=done_task,
            waiter_task=done_task,
            claude_client=SimpleNamespace(),
            login_prompt_sent=True,
        )
        opencode_flow = AgentAuthFlow(
            flow_id="flow-opencode-priority",
            backend="opencode",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=None,
            reader_task=done_task,
            waiter_task=done_task,
            provider="opencode",
            awaiting_code=True,
        )
        service._flows[claude_flow.flow_key] = claude_flow
        service._flows[opencode_flow.flow_key] = opencode_flow
        service.submit_code = AsyncMock()

        consumed = await service.maybe_consume_setup_reply(context, "auth-code#oauth-state")

        self.assertTrue(consumed)
        service.submit_code.assert_awaited_once_with(context, "auth-code#oauth-state", backend_hint="claude")

    async def test_maybe_consume_setup_reply_accepts_plain_opencode_key_while_waiting(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-opencode-plain",
            backend="opencode",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            pty_master_fd=11,
            awaiting_code=True,
            provider="opencode",
        )
        service._flows[flow.flow_key] = flow
        service.submit_code = AsyncMock()

        consumed = await service.maybe_consume_setup_reply(context, "sk-opencode-secret")

        self.assertTrue(consumed)
        service.submit_code.assert_awaited_once_with(context, "sk-opencode-secret", backend_hint="opencode")

    async def test_maybe_consume_setup_reply_accepts_non_sk_opencode_credential(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-opencode-alt-cred",
            backend="opencode",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            pty_master_fd=11,
            awaiting_code=True,
            provider="opencode",
        )
        service._flows[flow.flow_key] = flow
        service.submit_code = AsyncMock()

        consumed = await service.maybe_consume_setup_reply(context, "oc_live_Abcdef1234567890")

        self.assertTrue(consumed)
        service.submit_code.assert_awaited_once_with(context, "oc_live_Abcdef1234567890", backend_hint="opencode")

    async def test_maybe_consume_setup_reply_ignores_noncredential_opencode_plain_text(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-opencode-ignore",
            backend="opencode",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            pty_master_fd=11,
            awaiting_code=True,
            provider="opencode",
        )
        service._flows[flow.flow_key] = flow
        service.submit_code = AsyncMock()

        consumed = await service.maybe_consume_setup_reply(context, "hello world")

        self.assertFalse(consumed)
        service.submit_code.assert_not_awaited()

    async def test_maybe_consume_setup_reply_ignores_spaced_opencode_plain_text(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-opencode-space-ignore",
            backend="opencode",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            pty_master_fd=11,
            awaiting_code=True,
            provider="opencode",
        )
        service._flows[flow.flow_key] = flow
        service.submit_code = AsyncMock()

        consumed = await service.maybe_consume_setup_reply(context, "this is still normal chat")

        self.assertFalse(consumed)
        service.submit_code.assert_not_awaited()

    async def test_maybe_consume_setup_reply_ignores_separator_only_opencode_text(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-opencode-separator-ignore",
            backend="opencode",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            pty_master_fd=11,
            awaiting_code=True,
            provider="opencode",
        )
        service._flows[flow.flow_key] = flow
        service.submit_code = AsyncMock()

        consumed = await service.maybe_consume_setup_reply(context, "--------------------")

        self.assertFalse(consumed)
        service.submit_code.assert_not_awaited()

    async def test_maybe_consume_setup_reply_ignores_plain_text_without_callback_shape(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-claude-plain-ignore",
            backend="claude",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=None,
            reader_task=done_task,
            waiter_task=done_task,
            claude_client=SimpleNamespace(),
            login_prompt_sent=True,
        )
        service._flows[flow.flow_key] = flow
        service.submit_code = AsyncMock()

        consumed = await service.maybe_consume_setup_reply(context, "hello world")

        self.assertFalse(consumed)
        service.submit_code.assert_not_awaited()

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
            process=None,
            reader_task=done_task,
            waiter_task=done_task,
            claude_client=SimpleNamespace(),
        )
        replacement_flow = AgentAuthFlow(
            flow_id="flow-new",
            backend="claude",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=None,
            reader_task=done_task,
            waiter_task=done_task,
            claude_client=SimpleNamespace(),
        )
        service._flows[existing_flow.flow_key] = replacement_flow
        service._flows_by_id[existing_flow.flow_id] = existing_flow
        service._flows_by_id[replacement_flow.flow_id] = replacement_flow

        service._drop_flow(existing_flow)

        self.assertIs(service._flows[existing_flow.flow_key], replacement_flow)
        self.assertNotIn(existing_flow.flow_id, service._flows_by_id)
        self.assertIs(service._flows_by_id[replacement_flow.flow_id], replacement_flow)

    async def test_wait_for_claude_completion_refreshes_runtime_on_success(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-claude-wait",
            backend="claude",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=None,
            reader_task=done_task,
            waiter_task=done_task,
            claude_client=SimpleNamespace(),
        )
        service._flows[flow.flow_key] = flow
        service._flows_by_id[flow.flow_id] = flow
        service._send_claude_control_request = AsyncMock(return_value={})
        service._verify_login = AsyncMock(return_value=(True, '{"loggedIn": true}'))
        service._refresh_backend_runtime = AsyncMock()
        service._disconnect_claude_client = AsyncMock()

        await service._wait_for_claude_completion(flow)

        service._send_claude_control_request.assert_awaited_once_with(
            flow.claude_client,
            {"subtype": "claude_oauth_wait_for_completion"},
            timeout=service.setup_timeout_seconds,
        )
        service._refresh_backend_runtime.assert_awaited_once_with("claude")
        service._disconnect_claude_client.assert_awaited_once_with(flow.claude_client)
        self.assertIn("login is active again", controller.im_client.sent_messages[0][1].lower())
        self.assertNotIn(flow.flow_key, service._flows)

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

    async def test_refresh_backend_runtime_uses_backend_specific_runtime_refresh(self):
        controller = _StubController()
        controller.agent_service.agents["codex"] = SimpleNamespace(refresh_auth_state=AsyncMock())
        controller.agent_service.agents["claude"] = SimpleNamespace(refresh_auth_state=AsyncMock())
        service = AgentAuthService(controller)
        service._refresh_opencode_server = AsyncMock()

        await service._refresh_backend_runtime("codex")
        await service._refresh_backend_runtime("claude")
        await service._refresh_backend_runtime("opencode")

        controller.agent_service.agents["codex"].refresh_auth_state.assert_awaited_once()
        controller.agent_service.agents["claude"].refresh_auth_state.assert_awaited_once()
        service._refresh_opencode_server.assert_awaited_once()

    async def test_create_claude_control_client_sets_large_sdk_buffer(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        captured = {}

        class _StubClaudeAgentOptions:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

        class _StubClaudeSDKClient:
            def __init__(self, options):
                captured["options"] = options

            async def connect(self):
                captured["connected"] = True

        with (
            patch("core.agent_auth_service.ClaudeAgentOptions", _StubClaudeAgentOptions),
            patch("core.agent_auth_service.ClaudeSDKClient", _StubClaudeSDKClient),
        ):
            await service._create_claude_control_client(context)

        self.assertTrue(captured["connected"])
        self.assertEqual(captured["options"].max_buffer_size, CLAUDE_SDK_MAX_BUFFER_SIZE)

    async def test_verify_login_reports_opencode_segmentation_fault(self):
        controller = _StubController()
        service = AgentAuthService(controller)
        context = MessageContext(user_id="U1", channel_id="C1")
        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        flow = AgentAuthFlow(
            flow_id="flow-opencode-verify",
            backend="opencode",
            settings_key="C1",
            initiator_user_id="U1",
            context=context,
            process=SimpleNamespace(returncode=None),
            reader_task=done_task,
            waiter_task=done_task,
            provider="openai",
        )

        fake_process = SimpleNamespace(
            returncode=-11,
            communicate=AsyncMock(return_value=(b"Segmentation fault\n", b"")),
        )

        with patch("core.agent_auth_service.asyncio.create_subprocess_exec", AsyncMock(return_value=fake_process)):
            ok, detail = await service._verify_login(flow)

        self.assertFalse(ok)
        self.assertIn("Segmentation fault", detail)


class ClassifyAuthErrorTests(unittest.TestCase):
    def test_codex_401_requires_reset(self):
        self.assertTrue(classify_auth_error("codex", "unexpected status 401 Unauthorized"))

    def test_codex_missing_token_data_requires_reset(self):
        self.assertTrue(classify_auth_error("codex", "Codex turn failed: Token data is not available."))

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
