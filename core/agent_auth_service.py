"""Agent OAuth setup orchestration for remote IM-driven login recovery."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import re
import signal
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from modules.claude_sdk_compat import CLAUDE_SDK_AVAILABLE, ClaudeAgentOptions, ClaudeSDKClient
from modules.im import InlineButton, InlineKeyboard, MessageContext
from vibe.i18n import t as i18n_t
from vibe.opencode_config import remove_opencode_provider_api_key

logger = logging.getLogger(__name__)

ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
CODEX_URL_RE = re.compile(r"https://auth\.openai\.com/codex/device")
URL_RE = re.compile(r"https?://\S+")
CODEX_DEVICE_CODE_RE = re.compile(r"\b[A-Z0-9]{4}(?:-[A-Z0-9]{4,})+\b")
OPENCODE_API_KEY_PROMPT_RE = re.compile(r"enteryourapikey", re.IGNORECASE)
OPENCODE_CREDENTIAL_COUNT_RE = re.compile(r"\b(\d+)\s+credential(?:s)?\b", re.IGNORECASE)
CLAUDE_LOGIN_METHODS = {"claudeai", "console"}
OPENCODE_DIRECT_SETUP_URLS = {"opencode": "https://opencode.ai/auth"}


def classify_auth_error(backend: str, error_text: str) -> bool:
    """Return True when the error likely requires an OAuth reset."""
    text = (error_text or "").strip().lower()
    if not text:
        return False

    if backend == "codex":
        needles = (
            "401",
            "unauthorized",
            "not logged in",
            "login required",
            "authentication",
            "oauth",
            "token data is not available",
        )
        return any(needle in text for needle in needles)

    if backend == "claude":
        needles = (
            "401",
            "unauthorized",
            "oauth",
            "re-auth",
            "re-authenticate",
            "auth login",
            "login",
            "logged out",
        )
        return any(needle in text for needle in needles)

    if backend == "opencode":
        needles = (
            "401",
            "unauthorized",
            "authentication",
            "credential",
            "api key",
            "provider",
            "failed to send message: 401",
            "failed to start async prompt: 401",
        )
        return any(needle in text for needle in needles)

    return False


def sanitize_process_output(text: str) -> str:
    """Strip ANSI/control sequences so parsing works across TTY and non-TTY flows."""
    cleaned = ANSI_ESCAPE_RE.sub("", text)
    cleaned = CONTROL_CHAR_RE.sub("", cleaned)
    return cleaned.strip()


def verify_opencode_auth_list_output(text: str, provider: str | None = None) -> bool:
    """Return True when `opencode auth list` shows credentials for the target provider."""
    normalized_lines = []
    for line in sanitize_process_output(text).splitlines():
        stripped = re.sub(r"^[^\w]+", "", line).strip()
        if stripped:
            normalized_lines.append(stripped.lower())

    if provider:
        provider_pattern = re.compile(rf"\b{re.escape(provider.lower())}\b")
        negative_markers = (
            "0 credential",
            "no credential",
            "not configured",
            "logged out",
            "unauthenticated",
            "missing",
        )
        for line in normalized_lines:
            if line.startswith("credentials "):
                continue
            if not provider_pattern.search(line):
                continue
            count_match = OPENCODE_CREDENTIAL_COUNT_RE.search(line)
            if count_match:
                return int(count_match.group(1)) > 0
            return not any(marker in line for marker in negative_markers)
        return False

    normalized = "\n".join(line for line in normalized_lines if not line.startswith("credentials "))
    count_matches = [int(match.group(1)) for match in OPENCODE_CREDENTIAL_COUNT_RE.finditer(normalized)]
    if count_matches:
        return any(count > 0 for count in count_matches)
    return "credential" in normalized and "0 credentials" not in normalized and "no credentials" not in normalized


@dataclass
class AgentAuthFlow:
    flow_id: str
    backend: str
    settings_key: str
    initiator_user_id: str
    context: MessageContext
    process: asyncio.subprocess.Process | None
    reader_task: asyncio.Task[None]
    waiter_task: asyncio.Task[None]
    claude_client: ClaudeSDKClient | None = None
    pty_master_fd: int | None = None
    awaiting_code: bool = False
    login_prompt_sent: bool = False
    code_prompt_sent: bool = False
    url: str | None = None
    device_code: str | None = None
    provider: str | None = None
    last_status_text: str | None = None

    @property
    def flow_key(self) -> str:
        return f"{self.settings_key}:{self.backend}"


class AgentAuthService:
    """Manage backend-specific login flows triggered through IM."""

    def __init__(self, controller):
        self.controller = controller
        self._flows: dict[str, AgentAuthFlow] = {}
        self._flows_by_id: dict[str, AgentAuthFlow] = {}
        self._flow_lock = asyncio.Lock()

    def _t(self, key: str, **kwargs) -> str:
        lang = getattr(self.controller, "_get_lang", lambda: getattr(self.controller.config, "language", "en"))()
        return i18n_t(key, lang, **kwargs)

    def _get_im_client(self, context: MessageContext):
        getter = getattr(self.controller, "get_im_client_for_context", None)
        if callable(getter):
            return getter(context)
        return self.controller.im_client

    def _get_settings_key(self, context: MessageContext) -> str:
        return self.controller._get_settings_key(context)

    def _make_flow_key(self, context: MessageContext, backend: str) -> str:
        return f"{self._get_settings_key(context)}:{backend}"

    def _get_cli_binary(self, backend: str) -> str:
        agents_cfg = getattr(getattr(self.controller, "config", None), "agents", None)
        backend_cfg = getattr(agents_cfg, backend, None) if agents_cfg is not None else None
        cli_path = getattr(backend_cfg, "cli_path", None)
        return cli_path or backend

    async def _resolve_opencode_provider(self, context: MessageContext) -> str:
        override_agent = None
        override_model = None
        get_overrides = getattr(self.controller, "get_opencode_overrides", None)
        if callable(get_overrides):
            override_agent, override_model, _ = get_overrides(context)

        if isinstance(override_model, str) and "/" in override_model:
            return override_model.split("/", 1)[0]

        agent_service = getattr(self.controller, "agent_service", None)
        opencode_agent = getattr(agent_service, "agents", {}).get("opencode") if agent_service else None
        if opencode_agent and hasattr(opencode_agent, "_get_server"):
            try:
                server = await opencode_agent._get_server()
                runtime_provider = await self._resolve_opencode_provider_from_existing_session(context, server)
                if runtime_provider:
                    return runtime_provider
                agent_to_use = override_agent or server.get_default_agent_from_config()
                model_str = server.get_agent_model_from_config(agent_to_use)
                if isinstance(model_str, str) and "/" in model_str:
                    return model_str.split("/", 1)[0]
            except Exception as err:  # noqa: BLE001
                logger.info("Falling back to default OpenCode provider after lookup failure: %s", err)

        return "opencode"

    async def _resolve_opencode_provider_from_existing_session(self, context: MessageContext, server) -> str | None:
        session_handler = getattr(self.controller, "session_handler", None)
        sessions = getattr(self.controller, "sessions", None)
        if session_handler is None or sessions is None:
            return None

        get_info = getattr(session_handler, "get_session_info", None)
        if not callable(get_info):
            return None

        try:
            base_session_id, working_path, composite_key = get_info(context)
        except Exception as err:  # noqa: BLE001
            logger.debug("Failed to derive OpenCode session info for provider resolution: %s", err)
            return None

        session_key = self._get_settings_key(context)
        get_session_id = getattr(sessions, "get_agent_session_id", None)
        if not callable(get_session_id):
            return None
        session_id = get_session_id(session_key, composite_key, "opencode")
        if not session_id:
            session_id = get_session_id(session_key, base_session_id, "opencode")
        if not session_id:
            return None

        try:
            messages = await server.list_messages(session_id, working_path)
        except Exception as err:  # noqa: BLE001
            logger.debug("Failed to inspect OpenCode session %s for provider resolution: %s", session_id, err)
            return None

        for message in reversed(messages):
            provider = self._extract_opencode_message_provider(message)
            if provider:
                logger.info(
                    "Resolved OpenCode provider %s from existing session %s for %s",
                    provider,
                    session_id,
                    base_session_id,
                )
                return provider
        return None

    def _extract_opencode_message_provider(self, message: dict[str, Any]) -> str | None:
        info = message.get("info")
        if not isinstance(info, dict):
            return None

        direct_provider = info.get("providerID")
        if isinstance(direct_provider, str) and direct_provider:
            return direct_provider

        model = info.get("model")
        if isinstance(model, dict):
            model_provider = model.get("providerID")
            if isinstance(model_provider, str) and model_provider:
                return model_provider

        return None

    def _get_opencode_login_method(self, provider: str) -> str | None:
        if provider == "openai":
            return "ChatGPT Pro/Plus (headless)"
        return None

    def _supports_direct_opencode_api_key_setup(self, provider: str | None) -> bool:
        return provider in OPENCODE_DIRECT_SETUP_URLS

    def _get_opencode_setup_url(self, provider: str | None) -> str:
        return OPENCODE_DIRECT_SETUP_URLS.get(provider or "", "https://opencode.ai/auth")

    async def handle_setup_command(self, context: MessageContext, args: str = "") -> None:
        """Process `/setup`, `/setup <backend>`, or `/setup code <value>`."""
        parts = (args or "").strip().split(maxsplit=2)
        if parts and parts[0].lower() == "code":
            if len(parts) < 2 or not parts[1].strip():
                await self._send_message(context, f"❌ {self._t('command.setup.codeUsage')}")
                return
            if len(parts) == 2:
                await self.submit_code(context, parts[1].strip())
                return
            await self.submit_code(context, parts[2].strip(), backend_hint=parts[1].strip().lower())
            return

        backend_hint = parts[0].strip().lower() if parts else None
        claude_login_method = None
        if backend_hint in {"cc", "claude-code"}:
            backend_hint = "claude"
        elif backend_hint == "cx":
            backend_hint = "codex"

        if backend_hint in {"oc", "open-code"}:
            backend_hint = "opencode"

        if backend_hint == "claude" and len(parts) > 1:
            claude_login_method = self._normalize_claude_login_method(parts[1])
            if claude_login_method is None:
                await self._send_message(context, f"❌ {self._t('command.setup.claudeMethodUsage')}")
                return

        if backend_hint and backend_hint not in {"claude", "codex", "opencode"}:
            await self._send_message(context, f"❌ {self._t('command.setup.unsupportedBackend', backend=backend_hint)}")
            return

        await self.start_setup(
            context,
            backend=backend_hint or None,
            force_reset=True,
            claude_login_method=claude_login_method,
        )

    async def handle_setup_callback(self, context: MessageContext, callback_data: str) -> None:
        """Handle `auth_setup:*` callback buttons."""
        parts = callback_data.split(":")
        backend = parts[1].strip().lower() if len(parts) > 1 else None
        claude_login_method = self._normalize_claude_login_method(parts[2]) if len(parts) > 2 else None
        if backend == "auto":
            backend = None
        await self.start_setup(context, backend=backend, force_reset=True, claude_login_method=claude_login_method)

    async def start_setup(
        self,
        context: MessageContext,
        backend: str | None = None,
        force_reset: bool = True,
        claude_login_method: str | None = None,
    ) -> None:
        """Start an auth flow for the resolved backend."""
        resolved_backend = backend or self.controller.resolve_agent_for_context(context)
        if resolved_backend not in {"claude", "codex", "opencode"}:
            await self._send_message(
                context,
                f"❌ {self._t('command.setup.unsupportedBackend', backend=resolved_backend)}",
            )
            return

        if resolved_backend == "claude" and claude_login_method is None:
            await self._prompt_claude_login_method(context)
            return

        async with self._flow_lock:
            flow_key = self._make_flow_key(context, resolved_backend)
            existing = self._flows.get(flow_key)
            if existing:
                await self._terminate_flow(existing)

            await self._send_message(
                context,
                f"⏳ {self._t('command.setup.starting', backend=resolved_backend)}",
            )

            if resolved_backend == "codex":
                process = await self._start_codex_process(force_reset=force_reset)
                flow = AgentAuthFlow(
                    flow_id=uuid.uuid4().hex[:12],
                    backend=resolved_backend,
                    settings_key=self._get_settings_key(context),
                    initiator_user_id=context.user_id,
                    context=context,
                    process=process,
                    reader_task=asyncio.create_task(asyncio.sleep(0)),
                    waiter_task=asyncio.create_task(asyncio.sleep(0)),
                )
            elif resolved_backend == "claude":
                client, manual_url = await self._start_claude_control_flow(
                    context,
                    force_reset=force_reset,
                    login_with_claude_ai=claude_login_method != "console",
                )
                flow = AgentAuthFlow(
                    flow_id=uuid.uuid4().hex[:12],
                    backend=resolved_backend,
                    settings_key=self._get_settings_key(context),
                    initiator_user_id=context.user_id,
                    context=context,
                    process=None,
                    reader_task=asyncio.create_task(asyncio.sleep(0)),
                    waiter_task=asyncio.create_task(asyncio.sleep(0)),
                    claude_client=client,
                    login_prompt_sent=True,
                    url=manual_url,
                )
            else:
                provider = await self._resolve_opencode_provider(context)
                if self._supports_direct_opencode_api_key_setup(provider):
                    flow = AgentAuthFlow(
                        flow_id=uuid.uuid4().hex[:12],
                        backend=resolved_backend,
                        settings_key=self._get_settings_key(context),
                        initiator_user_id=context.user_id,
                        context=context,
                        process=None,
                        reader_task=asyncio.create_task(asyncio.sleep(0)),
                        waiter_task=asyncio.create_task(asyncio.sleep(0)),
                        provider=provider,
                        awaiting_code=True,
                        login_prompt_sent=True,
                        code_prompt_sent=True,
                        url=self._get_opencode_setup_url(provider),
                    )
                else:
                    process, master_fd, provider = await self._start_opencode_process(context, force_reset=force_reset)
                    flow = AgentAuthFlow(
                        flow_id=uuid.uuid4().hex[:12],
                        backend=resolved_backend,
                        settings_key=self._get_settings_key(context),
                        initiator_user_id=context.user_id,
                        context=context,
                        process=process,
                        reader_task=asyncio.create_task(asyncio.sleep(0)),
                        waiter_task=asyncio.create_task(asyncio.sleep(0)),
                        pty_master_fd=master_fd,
                        provider=provider,
                    )

            self._flows[flow_key] = flow
            self._flows_by_id[flow.flow_id] = flow
            if resolved_backend == "codex":
                flow.reader_task = asyncio.create_task(self._read_codex_output(process, context, resolved_backend))
                flow.waiter_task = asyncio.create_task(self._wait_for_completion(flow))
            elif resolved_backend == "claude":
                flow.waiter_task = asyncio.create_task(self._wait_for_claude_completion(flow))
                await self._send_message(
                    flow.context,
                    self._t("command.setup.claudeInstructions", url=manual_url),
                )
            else:
                if self._supports_direct_opencode_api_key_setup(flow.provider):
                    await self._send_message(
                        flow.context,
                        self._t(
                            "command.setup.opencodeInstructions",
                            provider=flow.provider or "opencode",
                            url=flow.url or self._get_opencode_setup_url(flow.provider),
                        ),
                    )
                else:
                    assert flow.pty_master_fd is not None
                    flow.reader_task = asyncio.create_task(
                        self._read_pty_output(process, flow.pty_master_fd, context, resolved_backend)
                    )
                    flow.waiter_task = asyncio.create_task(self._wait_for_completion(flow))

    async def submit_code(self, context: MessageContext, code: str, backend_hint: str | None = None) -> None:
        """Submit follow-up code to an active auth flow."""
        flow = self._find_flow_for_submission(context, backend_hint)
        if flow is None:
            await self._send_message(context, f"❌ {self._t('command.setup.noActiveFlow')}")
            return
        if flow.initiator_user_id != context.user_id:
            await self._send_message(context, f"❌ {self._t('command.setup.notFlowOwner')}")
            return
        if flow.backend == "claude":
            if flow.claude_client is None:
                await self._send_message(context, f"❌ {self._t('command.setup.codeNotSupported')}")
                return
            if not self._allows_proactive_code_submission(flow):
                await self._send_message(context, f"❌ {self._t('command.setup.notAwaitingCode')}")
                return

            callback = self._parse_claude_callback_code(code)
            if callback is None:
                await self._send_message(context, f"❌ {self._t('command.setup.claudeCallbackUsage')}")
                return

            authorization_code, state = callback
            await self._send_claude_callback(flow.claude_client, authorization_code, state)
            await self._send_message(context, f"✅ {self._t('command.setup.claudeCallbackSubmitted')}")
            return

        if flow.backend != "opencode":
            await self._send_message(context, f"❌ {self._t('command.setup.codeNotSupported')}")
            return
        normalized_code = code.strip()
        if self._supports_direct_opencode_api_key_setup(flow.provider):
            await self._install_opencode_api_key(flow.provider or "opencode", normalized_code)
            flow.awaiting_code = False
            await self._refresh_backend_runtime("opencode")
            await self._clear_backend_sessions_for_context("opencode", context)
            await self._send_message(context, f"✅ {self._t('command.setup.success', backend=flow.backend)}")
            self._drop_flow(flow)
            return
        if flow.pty_master_fd is None:
            await self._send_message(context, f"❌ {self._t('command.setup.codeNotSupported')}")
            return
        if not flow.awaiting_code and not self._allows_proactive_code_submission(flow):
            await self._send_message(context, f"❌ {self._t('command.setup.notAwaitingCode')}")
            return

        await asyncio.to_thread(os.write, flow.pty_master_fd, f"{normalized_code}\n".encode("utf-8"))
        flow.awaiting_code = False
        await self._send_message(context, f"✅ {self._t('command.setup.codeSubmitted', backend=flow.backend)}")

    async def maybe_consume_setup_reply(self, context: MessageContext, message: str) -> bool:
        """Intercept plain-text replies for active setup flows before normal agent routing."""
        if not message or message.lstrip().startswith("/"):
            return False

        flow = self._find_flow_for_submission(context, "claude")
        if flow is not None and flow.backend == "claude" and flow.initiator_user_id == context.user_id:
            if self._allows_proactive_code_submission(flow) and self._parse_claude_callback_code(message) is not None:
                await self.submit_code(context, message, backend_hint="claude")
                return True

        opencode_flow = self._find_flow_for_submission(context, "opencode")
        if (
            opencode_flow is not None
            and opencode_flow.backend == "opencode"
            and opencode_flow.initiator_user_id == context.user_id
            and opencode_flow.awaiting_code
            and self._looks_like_direct_opencode_credential(message)
        ):
            await self.submit_code(context, message.strip(), backend_hint="opencode")
            return True

        return False

    async def maybe_emit_auth_recovery_message(self, context: MessageContext, backend: str, error_text: str) -> bool:
        """Emit a reset-oauth button when the backend error is auth-related."""
        if not classify_auth_error(backend, error_text):
            return False

        await self._send_message_with_button(
            context,
            f"{error_text}\n\n{self._t('command.setup.resetPrompt', backend=backend)}",
            button_text=self._t("button.resetOAuth"),
            callback_data=f"auth_setup:{backend}",
        )
        return True

    async def _send_message(self, context: MessageContext, text: str) -> Optional[str]:
        return await self._get_im_client(context).send_message(context, text)

    async def _send_message_with_button(
        self,
        context: MessageContext,
        text: str,
        *,
        button_text: str,
        callback_data: str,
    ) -> Optional[str]:
        keyboard = InlineKeyboard(buttons=[[InlineButton(text=button_text, callback_data=callback_data)]])
        return await self._send_message_with_keyboard(context, text, keyboard)

    async def _send_message_with_keyboard(
        self,
        context: MessageContext,
        text: str,
        keyboard: InlineKeyboard,
        *,
        fallback_text: str | None = None,
    ) -> Optional[str]:
        im_client = self._get_im_client(context)
        if hasattr(im_client, "send_message_with_buttons"):
            button_text = text if not fallback_text else f"{text}\n\n{fallback_text}"
            return await im_client.send_message_with_buttons(context, button_text, keyboard)
        fallback = fallback_text or text
        return await im_client.send_message(context, fallback)

    async def _prompt_claude_login_method(self, context: MessageContext) -> None:
        text = self._t("command.setup.claudeMethodPrompt")
        keyboard = InlineKeyboard(
            buttons=[
                [
                    InlineButton(text=self._t("button.claudeAi"), callback_data="auth_setup:claude:claudeai"),
                    InlineButton(text=self._t("button.console"), callback_data="auth_setup:claude:console"),
                ]
            ]
        )
        fallback_text = self._t("command.setup.claudeMethodFallback")
        await self._send_message_with_keyboard(context, text, keyboard, fallback_text=fallback_text)

    async def _start_codex_process(self, *, force_reset: bool) -> asyncio.subprocess.Process:
        binary = self._get_cli_binary("codex")
        if force_reset:
            await self._run_utility_command(binary, "logout")
        return await asyncio.create_subprocess_exec(
            binary,
            "login",
            "--device-auth",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

    async def _start_claude_control_flow(
        self,
        context: MessageContext,
        *,
        force_reset: bool,
        login_with_claude_ai: bool,
    ) -> tuple[ClaudeSDKClient, str]:
        if not CLAUDE_SDK_AVAILABLE:
            raise ModuleNotFoundError("claude_agent_sdk is required for Claude setup flows")

        if force_reset:
            await self._run_utility_command(self._get_cli_binary("claude"), "auth", "logout")

        client = await self._create_claude_control_client(context)
        try:
            response = await self._send_claude_control_request(
                client,
                {
                    "subtype": "claude_authenticate",
                    "loginWithClaudeAi": login_with_claude_ai,
                },
            )
        except Exception:
            await self._disconnect_claude_client(client)
            raise

        manual_url = str(response.get("manualUrl") or "").strip()
        if not manual_url:
            await self._disconnect_claude_client(client)
            raise RuntimeError("Claude auth flow did not return a manual login URL")
        return client, manual_url

    async def _create_claude_control_client(self, context: MessageContext) -> ClaudeSDKClient:
        session_handler = getattr(self.controller, "session_handler", None)
        get_working_path = getattr(session_handler, "get_working_path", None)
        if callable(get_working_path):
            working_path = get_working_path(context)
        else:
            working_path = os.getcwd()

        if not os.path.exists(working_path):
            os.makedirs(working_path, exist_ok=True)

        claude_env = {}
        for key in os.environ:
            if key.startswith("ANTHROPIC_") or key.startswith("CLAUDE_"):
                claude_env[key] = os.environ[key]

        should_force_sandbox = getattr(session_handler, "_should_force_claude_sandbox", None)
        if callable(should_force_sandbox) and should_force_sandbox():
            claude_env["IS_SANDBOX"] = "1"

        option_kwargs = {
            "cwd": working_path,
            "env": claude_env,
            "setting_sources": ["user"],
        }
        permission_mode = getattr(getattr(self.controller.config, "claude", None), "permission_mode", None)
        if permission_mode:
            option_kwargs["permission_mode"] = permission_mode

        get_cli_override = getattr(session_handler, "_get_claude_cli_path_override", None)
        cli_override = get_cli_override() if callable(get_cli_override) else None
        if cli_override:
            option_kwargs["cli_path"] = cli_override

        client = ClaudeSDKClient(options=ClaudeAgentOptions(**option_kwargs))
        await client.connect()
        return client

    async def _send_claude_control_request(
        self,
        client: ClaudeSDKClient,
        request: dict[str, object],
        *,
        timeout: float = 900.0,
    ) -> dict[str, object]:
        query = getattr(client, "_query", None)
        sender = getattr(query, "_send_control_request", None)
        if not callable(sender):
            raise RuntimeError("Claude SDK control channel is not available")
        response = await sender(request, timeout=timeout)
        return response if isinstance(response, dict) else {}

    async def _send_claude_callback(
        self,
        client: ClaudeSDKClient,
        authorization_code: str,
        state: str,
    ) -> None:
        transport = getattr(client, "_transport", None)
        if transport is None or not hasattr(transport, "write"):
            raise RuntimeError("Claude SDK transport is not available")

        message = {
            "type": "control_request",
            "request_id": f"auth-callback-{uuid.uuid4().hex}",
            "request": {
                "subtype": "claude_oauth_callback",
                "authorizationCode": authorization_code,
                "state": state,
            },
        }
        await transport.write(json.dumps(message) + "\n")

    async def _disconnect_claude_client(self, client: ClaudeSDKClient) -> None:
        disconnect = getattr(client, "disconnect", None)
        close = getattr(client, "close", None)
        try:
            if callable(disconnect):
                await disconnect()
            elif callable(close):
                await close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error disconnecting Claude auth client: %s", exc)

    async def _start_opencode_process(
        self,
        context: MessageContext,
        *,
        force_reset: bool,
    ) -> tuple[asyncio.subprocess.Process, int, str]:
        binary = self._get_cli_binary("opencode")
        provider = await self._resolve_opencode_provider(context)
        method = self._get_opencode_login_method(provider)
        # OpenCode auth is provider-scoped and may keep multiple credentials.
        # `opencode auth logout` can become interactive, so refresh by re-running
        # login for the target provider instead of forcing a global logout.
        master_fd, slave_fd = os.openpty()
        try:
            cmd = [
                binary,
                "auth",
                "login",
                "-p",
                provider,
            ]
            if method:
                cmd.extend(["-m", method])
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
            )
        except Exception:
            os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        return process, master_fd, provider

    async def _run_utility_command(self, *cmd: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            await asyncio.wait_for(process.communicate(), timeout=20)
        except Exception as err:  # noqa: BLE001
            logger.info("Ignoring setup preflight command failure for %s: %s", " ".join(cmd), err)

    async def _read_codex_output(
        self,
        process: asyncio.subprocess.Process,
        context: MessageContext,
        backend: str,
    ) -> None:
        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            await self._handle_process_text(context, backend, line.decode("utf-8", errors="replace"))

    async def _read_pty_output(
        self,
        process: asyncio.subprocess.Process,
        master_fd: int,
        context: MessageContext,
        backend: str,
    ) -> None:
        try:
            os.set_blocking(master_fd, False)
            while True:
                try:
                    chunk = os.read(master_fd, 4096)
                except BlockingIOError:
                    if process.returncode is not None:
                        break
                    await asyncio.sleep(0.05)
                    continue
                except OSError as err:
                    if err.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    if process.returncode is not None:
                        break
                    await asyncio.sleep(0.05)
                    continue
                await self._handle_process_text(context, backend, chunk.decode("utf-8", errors="replace"))
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass

    async def _handle_process_text(self, context: MessageContext, backend: str, text: str) -> None:
        flow = self._flows.get(self._make_flow_key(context, backend))
        if flow is None:
            return

        clean = sanitize_process_output(text)
        if not clean:
            return

        flow.last_status_text = clean

        if backend == "codex":
            maybe_url = CODEX_URL_RE.search(clean)
            maybe_code = CODEX_DEVICE_CODE_RE.search(clean)
            if maybe_url:
                flow.url = maybe_url.group(0)
            if maybe_code:
                flow.device_code = maybe_code.group(0)
            if flow.url and flow.device_code and not flow.login_prompt_sent:
                flow.login_prompt_sent = True
                await self._send_message(
                    flow.context,
                    self._t(
                        "command.setup.codexInstructions",
                        url=flow.url,
                        code=flow.device_code,
                    ),
                )
            return

        maybe_url = URL_RE.search(clean)
        compact = re.sub(r"\s+", "", clean).lower()

        if backend == "claude":
            return

        maybe_code = CODEX_DEVICE_CODE_RE.search(clean)
        if maybe_url:
            flow.url = maybe_url.group(0)
        if maybe_code:
            flow.device_code = maybe_code.group(0)
        if flow.provider == "openai" and flow.url and flow.device_code and not flow.login_prompt_sent:
            flow.login_prompt_sent = True
            await self._send_message(
                flow.context,
                self._t(
                    "command.setup.opencodeDeviceInstructions",
                    url=flow.url,
                    code=flow.device_code,
                ),
            )
            return

        if flow.url and not flow.login_prompt_sent and flow.provider != "openai":
            flow.login_prompt_sent = True
            await self._send_message(
                flow.context,
                self._t(
                    "command.setup.opencodeInstructions",
                    provider=flow.provider or "opencode",
                    url=flow.url,
                ),
            )

        if OPENCODE_API_KEY_PROMPT_RE.search(compact):
            was_awaiting_code = flow.awaiting_code
            flow.awaiting_code = True
            prompt_key = "command.setup.opencodeCodePrompt"
            if flow.code_prompt_sent:
                if was_awaiting_code:
                    return
                prompt_key = "command.setup.opencodeCodeRetryPrompt"
            else:
                flow.code_prompt_sent = True

            await self._send_message(
                flow.context,
                self._t(
                    prompt_key,
                    provider=flow.provider or "opencode",
                ),
            )

    async def _wait_for_completion(self, flow: AgentAuthFlow) -> None:
        try:
            assert flow.process is not None
            await flow.process.wait()
            await flow.reader_task
            ok, detail = await self._verify_login(flow)
            if ok:
                await self._refresh_backend_runtime(flow.backend)
                await self._send_message(
                    flow.context,
                    f"✅ {self._t('command.setup.success', backend=flow.backend)}",
                )
            else:
                detail_text = detail or self._t("command.setup.unknownFailure")
                await self._send_message_with_button(
                    flow.context,
                    f"❌ {self._t('command.setup.failed', backend=flow.backend, detail=detail_text)}",
                    button_text=self._t("button.resetOAuth"),
                    callback_data=f"auth_setup:{flow.backend}",
                )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            logger.error("Agent auth flow failed for %s: %s", flow.backend, err, exc_info=True)
            await self._send_message_with_button(
                flow.context,
                f"❌ {self._t('command.setup.failed', backend=flow.backend, detail=str(err))}",
                button_text=self._t("button.resetOAuth"),
                callback_data=f"auth_setup:{flow.backend}",
            )
        finally:
            self._drop_flow(flow)

    async def _wait_for_claude_completion(self, flow: AgentAuthFlow) -> None:
        try:
            if flow.claude_client is None:
                raise RuntimeError("Claude auth flow is missing its SDK client")

            await self._send_claude_control_request(
                flow.claude_client,
                {"subtype": "claude_oauth_wait_for_completion"},
            )
            ok, detail = await self._verify_login(flow)
            if ok:
                await self._refresh_backend_runtime(flow.backend)
                await self._send_message(
                    flow.context,
                    f"✅ {self._t('command.setup.success', backend=flow.backend)}",
                )
            else:
                detail_text = detail or self._t("command.setup.unknownFailure")
                await self._send_message_with_button(
                    flow.context,
                    f"❌ {self._t('command.setup.failed', backend=flow.backend, detail=detail_text)}",
                    button_text=self._t("button.resetOAuth"),
                    callback_data=f"auth_setup:{flow.backend}",
                )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            logger.error("Claude auth flow failed: %s", err, exc_info=True)
            await self._send_message_with_button(
                flow.context,
                f"❌ {self._t('command.setup.failed', backend=flow.backend, detail=str(err))}",
                button_text=self._t("button.resetOAuth"),
                callback_data=f"auth_setup:{flow.backend}",
            )
        finally:
            if flow.claude_client is not None:
                await self._disconnect_claude_client(flow.claude_client)
            self._drop_flow(flow)

    async def _verify_login(self, flow: AgentAuthFlow) -> tuple[bool, str]:
        backend = flow.backend
        if backend == "codex":
            binary = self._get_cli_binary("codex")
            process = await asyncio.create_subprocess_exec(
                binary,
                "login",
                "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
            text = stdout.decode("utf-8", errors="replace").strip()
            return ("not logged in" not in text.lower(), text)

        if backend == "opencode":
            binary = self._get_cli_binary("opencode")
            process = await asyncio.create_subprocess_exec(
                binary,
                "auth",
                "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
            text = stdout.decode("utf-8", errors="replace").strip()
            if process.returncode and process.returncode != 0:
                return False, self._describe_opencode_cli_failure(process.returncode, text)
            return (verify_opencode_auth_list_output(text, flow.provider), text)

        binary = self._get_cli_binary("claude")
        process = await asyncio.create_subprocess_exec(
            binary,
            "auth",
            "status",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        text = stdout.decode("utf-8", errors="replace").strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return False, text
        return (bool(payload.get("loggedIn")), text)

    def _describe_opencode_cli_failure(self, returncode: int, text: str) -> str:
        detail = text or ""
        lowered = detail.lower()
        if "segmentation fault" in lowered or returncode == -signal.SIGSEGV:
            return "OpenCode CLI crashed with Segmentation fault during auth verification."
        if returncode < 0:
            try:
                signal_name = signal.Signals(-returncode).name
            except ValueError:
                signal_name = f"signal {-returncode}"
            return f"OpenCode CLI crashed during auth verification ({signal_name})."
        if detail:
            return detail
        return f"OpenCode auth verification failed with exit code {returncode}."

    async def _install_opencode_api_key(self, provider: str, api_key: str) -> None:
        agent_service = getattr(self.controller, "agent_service", None)
        opencode_agent = getattr(agent_service, "agents", {}).get("opencode") if agent_service else None
        if not opencode_agent or not hasattr(opencode_agent, "_get_server"):
            raise RuntimeError("OpenCode agent is not available for auth setup.")

        server = await opencode_agent._get_server()
        setter = getattr(server, "set_api_key_auth", None)
        if not callable(setter):
            raise RuntimeError("OpenCode server does not support non-interactive auth setup.")
        await setter(provider, api_key)
        try:
            await asyncio.to_thread(
                remove_opencode_provider_api_key,
                provider,
                logger_instance=logger,
            )
        except Exception as err:  # noqa: BLE001
            logger.warning("Failed to clean legacy OpenCode provider config for %s: %s", provider, err)

    async def _refresh_opencode_server(self) -> None:
        agent_service = getattr(self.controller, "agent_service", None)
        opencode_agent = getattr(agent_service, "agents", {}).get("opencode") if agent_service else None
        if not opencode_agent or not hasattr(opencode_agent, "_get_server"):
            return
        server = await opencode_agent._get_server()
        if hasattr(server, "restart_for_auth_refresh"):
            await server.restart_for_auth_refresh()

    async def _refresh_backend_runtime(self, backend: str) -> None:
        if backend == "opencode":
            await self._refresh_opencode_server()
            return

        agent_service = getattr(self.controller, "agent_service", None)
        agent = getattr(agent_service, "agents", {}).get(backend) if agent_service else None
        refresh = getattr(agent, "refresh_auth_state", None)
        if callable(refresh):
            await refresh()

    async def _clear_backend_sessions_for_context(self, backend: str, context: MessageContext) -> None:
        agent_service = getattr(self.controller, "agent_service", None)
        agent = getattr(agent_service, "agents", {}).get(backend) if agent_service else None
        clear_sessions = getattr(agent, "clear_sessions", None)
        if not callable(clear_sessions):
            return
        await clear_sessions(self._get_settings_key(context))

    def _find_flow_for_submission(self, context: MessageContext, backend_hint: str | None) -> AgentAuthFlow | None:
        settings_key = self._get_settings_key(context)
        if backend_hint:
            return self._flows.get(f"{settings_key}:{backend_hint}")

        candidates = [
            flow
            for flow in self._flows.values()
            if flow.settings_key == settings_key and flow.initiator_user_id == context.user_id
        ]
        awaiting_candidates = [flow for flow in candidates if flow.awaiting_code]
        if awaiting_candidates:
            return awaiting_candidates[-1]

        code_capable_candidates = [
            flow
            for flow in candidates
            if (flow.backend == "claude" and flow.claude_client is not None)
            or (flow.backend == "opencode" and flow.pty_master_fd is not None)
        ]
        if code_capable_candidates:
            return code_capable_candidates[-1]

        return candidates[-1] if candidates else None

    def _allows_proactive_code_submission(self, flow: AgentAuthFlow) -> bool:
        return (
            flow.backend == "claude"
            and flow.claude_client is not None
            and flow.login_prompt_sent
        )

    def _parse_claude_callback_code(self, code: str) -> tuple[str, str] | None:
        authorization_code, separator, state = code.strip().partition("#")
        if separator != "#" or not authorization_code or not state:
            return None
        return authorization_code, state

    def _looks_like_direct_opencode_credential(self, text: str) -> bool:
        candidate = text.strip()
        if len(candidate) < 16 or any(ch.isspace() for ch in candidate):
            return False
        if URL_RE.search(candidate):
            return False

        alnum_count = sum(ch.isalnum() for ch in candidate)
        has_digit = any(ch.isdigit() for ch in candidate)
        has_upper = any(ch.isupper() for ch in candidate)
        has_lower = any(ch.islower() for ch in candidate)
        separator_count = sum(candidate.count(ch) for ch in ("-", "_", ".", ":", "="))

        if alnum_count < 8:
            return False

        if candidate.startswith("sk-"):
            return True

        if separator_count >= 2:
            return True

        return has_digit and ((has_upper and has_lower) or separator_count >= 1)

    def _normalize_claude_login_method(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        aliases = {
            "claude": "claudeai",
            "claude.ai": "claudeai",
            "claudeai": "claudeai",
            "subscription": "claudeai",
            "console": "console",
            "platform": "console",
            "platform.claude.com": "console",
        }
        mapped = aliases.get(normalized)
        return mapped if mapped in CLAUDE_LOGIN_METHODS else None

    async def _terminate_flow(self, flow: AgentAuthFlow) -> None:
        if flow.waiter_task and not flow.waiter_task.done():
            flow.waiter_task.cancel()
            try:
                await flow.waiter_task
            except asyncio.CancelledError:
                pass
        if flow.reader_task and not flow.reader_task.done():
            flow.reader_task.cancel()
            try:
                await flow.reader_task
            except asyncio.CancelledError:
                pass
        if flow.process is not None and flow.process.returncode is None:
            flow.process.terminate()
            try:
                await asyncio.wait_for(flow.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                flow.process.kill()
                await flow.process.wait()
        if flow.claude_client is not None:
            await self._disconnect_claude_client(flow.claude_client)
        self._drop_flow(flow)

    def _drop_flow(self, flow: AgentAuthFlow) -> None:
        if self._flows.get(flow.flow_key) is flow:
            self._flows.pop(flow.flow_key, None)
        self._flows_by_id.pop(flow.flow_id, None)
