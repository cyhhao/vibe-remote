"""Agent OAuth setup orchestration for remote IM-driven login recovery."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Optional

from modules.im import InlineButton, InlineKeyboard, MessageContext
from vibe.i18n import t as i18n_t

logger = logging.getLogger(__name__)

ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
CODEX_URL_RE = re.compile(r"https://auth\.openai\.com/codex/device")
URL_RE = re.compile(r"https?://\S+")
CODEX_DEVICE_CODE_RE = re.compile(r"\b[A-Z0-9]{4}(?:-[A-Z0-9]{4,})+\b")
CLAUDE_CODE_PROMPT_RE = re.compile(r"paste code here|if prompted", re.IGNORECASE)
OPENCODE_API_KEY_PROMPT_RE = re.compile(r"enteryourapikey", re.IGNORECASE)


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


@dataclass
class AgentAuthFlow:
    flow_id: str
    backend: str
    settings_key: str
    initiator_user_id: str
    context: MessageContext
    process: asyncio.subprocess.Process
    reader_task: asyncio.Task[None]
    waiter_task: asyncio.Task[None]
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
                agent_to_use = override_agent or server.get_default_agent_from_config()
                model_str = server.get_agent_model_from_config(agent_to_use)
                if isinstance(model_str, str) and "/" in model_str:
                    return model_str.split("/", 1)[0]
            except Exception as err:  # noqa: BLE001
                logger.info("Falling back to default OpenCode provider after lookup failure: %s", err)

        return "opencode"

    def _get_opencode_login_method(self, provider: str) -> str | None:
        if provider == "openai":
            return "ChatGPT Pro/Plus (headless)"
        return None

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
        if backend_hint in {"cc", "claude-code"}:
            backend_hint = "claude"
        elif backend_hint == "cx":
            backend_hint = "codex"

        if backend_hint in {"oc", "open-code"}:
            backend_hint = "opencode"

        if backend_hint and backend_hint not in {"claude", "codex", "opencode"}:
            await self._send_message(context, f"❌ {self._t('command.setup.unsupportedBackend', backend=backend_hint)}")
            return

        await self.start_setup(context, backend=backend_hint or None, force_reset=True)

    async def handle_setup_callback(self, context: MessageContext, callback_data: str) -> None:
        """Handle `auth_setup:*` callback buttons."""
        _, _, backend_hint = callback_data.partition(":")
        backend = backend_hint.strip().lower() or None
        if backend == "auto":
            backend = None
        await self.start_setup(context, backend=backend, force_reset=True)

    async def start_setup(self, context: MessageContext, backend: str | None = None, force_reset: bool = True) -> None:
        """Start an auth flow for the resolved backend."""
        resolved_backend = backend or self.controller.resolve_agent_for_context(context)
        if resolved_backend not in {"claude", "codex", "opencode"}:
            await self._send_message(
                context,
                f"❌ {self._t('command.setup.unsupportedBackend', backend=resolved_backend)}",
            )
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
                process, master_fd = await self._start_claude_process(force_reset=force_reset)
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
        if flow.backend not in {"claude", "opencode"} or flow.pty_master_fd is None:
            await self._send_message(context, f"❌ {self._t('command.setup.codeNotSupported')}")
            return
        if not flow.awaiting_code:
            await self._send_message(context, f"❌ {self._t('command.setup.notAwaitingCode')}")
            return

        await asyncio.to_thread(os.write, flow.pty_master_fd, f"{code.strip()}\n".encode("utf-8"))
        flow.awaiting_code = False
        await self._send_message(context, f"✅ {self._t('command.setup.codeSubmitted', backend=flow.backend)}")

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
        im_client = self._get_im_client(context)
        if hasattr(im_client, "send_message_with_buttons"):
            keyboard = InlineKeyboard(buttons=[[InlineButton(text=button_text, callback_data=callback_data)]])
            return await im_client.send_message_with_buttons(context, text, keyboard)
        fallback = f"{text}\n\n{self._t('command.setup.manualFallback', backend=callback_data.split(':', 1)[1])}"
        return await im_client.send_message(context, fallback)

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

    async def _start_claude_process(self, *, force_reset: bool) -> tuple[asyncio.subprocess.Process, int]:
        binary = self._get_cli_binary("claude")
        if force_reset:
            await self._run_utility_command(binary, "auth", "logout")

        master_fd, slave_fd = os.openpty()
        try:
            process = await asyncio.create_subprocess_exec(
                binary,
                "auth",
                "login",
                "--claudeai",
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
            )
        finally:
            os.close(slave_fd)
        return process, master_fd

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
            while process.returncode is None:
                chunk = await asyncio.to_thread(os.read, master_fd, 4096)
                if not chunk:
                    break
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
            if maybe_url and "claude.ai/oauth/authorize" in maybe_url.group(0):
                flow.url = maybe_url.group(0)
            if flow.url and not flow.login_prompt_sent:
                flow.login_prompt_sent = True
                await self._send_message(
                    flow.context,
                    self._t("command.setup.claudeInstructions", url=flow.url),
                )

            if CLAUDE_CODE_PROMPT_RE.search(clean) and not flow.code_prompt_sent:
                flow.awaiting_code = True
                flow.code_prompt_sent = True
                await self._send_message(
                    flow.context,
                    self._t("command.setup.claudeCodePrompt"),
                )
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

        if OPENCODE_API_KEY_PROMPT_RE.search(compact) and not flow.code_prompt_sent:
            flow.awaiting_code = True
            flow.code_prompt_sent = True
            await self._send_message(
                flow.context,
                self._t(
                    "command.setup.opencodeCodePrompt",
                    provider=flow.provider or "opencode",
                ),
            )

    async def _wait_for_completion(self, flow: AgentAuthFlow) -> None:
        try:
            await flow.process.wait()
            await flow.reader_task
            ok, detail = await self._verify_login(flow.backend)
            if ok:
                if flow.backend == "opencode":
                    await self._refresh_opencode_server()
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

    async def _verify_login(self, backend: str) -> tuple[bool, str]:
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
            normalized = text.lower()
            return ("credentials" in normalized and "0 credentials" not in normalized, text)

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

    async def _refresh_opencode_server(self) -> None:
        agent_service = getattr(self.controller, "agent_service", None)
        opencode_agent = getattr(agent_service, "agents", {}).get("opencode") if agent_service else None
        if not opencode_agent or not hasattr(opencode_agent, "_get_server"):
            return
        server = await opencode_agent._get_server()
        if hasattr(server, "restart_for_auth_refresh"):
            await server.restart_for_auth_refresh()

    def _find_flow_for_submission(self, context: MessageContext, backend_hint: str | None) -> AgentAuthFlow | None:
        settings_key = self._get_settings_key(context)
        if backend_hint:
            return self._flows.get(f"{settings_key}:{backend_hint}")

        for backend in ("claude", "codex", "opencode"):
            flow = self._flows.get(f"{settings_key}:{backend}")
            if flow is not None:
                return flow
        return None

    async def _terminate_flow(self, flow: AgentAuthFlow) -> None:
        if flow.waiter_task and not flow.waiter_task.done():
            flow.waiter_task.cancel()
        if flow.reader_task and not flow.reader_task.done():
            flow.reader_task.cancel()
        if flow.process.returncode is None:
            flow.process.terminate()
            try:
                await asyncio.wait_for(flow.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                flow.process.kill()
                await flow.process.wait()
        self._drop_flow(flow)

    def _drop_flow(self, flow: AgentAuthFlow) -> None:
        self._flows.pop(flow.flow_key, None)
        self._flows_by_id.pop(flow.flow_id, None)
