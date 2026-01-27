import asyncio
import json
import logging
import os
import signal
from asyncio.subprocess import Process
from typing import Dict, Optional, Tuple

from markdown_to_mrkdwn import SlackMarkdownConverter

from modules.agents.base import AgentRequest, BaseAgent

logger = logging.getLogger(__name__)

STREAM_BUFFER_LIMIT = 8 * 1024 * 1024  # 8MB cap for Codex stdout/stderr streams


class CodexAgent(BaseAgent):
    """Codex CLI integration via codex exec JSON streaming mode."""

    name = "codex"

    def __init__(self, controller, codex_config):
        super().__init__(controller)
        self.codex_config = codex_config
        self.active_processes: Dict[str, Tuple[Process, str]] = {}
        self.base_process_index: Dict[str, str] = {}
        self.composite_to_base: Dict[str, str] = {}
        self._initialized_sessions: set[str] = set()
        self._pending_assistant_messages: Dict[str, Tuple[str, Optional[str]]] = {}
        self._slack_markdown_converter = (
            SlackMarkdownConverter()
            if getattr(self.controller.config, "platform", None) == "slack"
            else None
        )

    async def handle_message(self, request: AgentRequest) -> None:
        existing = self.base_process_index.get(request.base_session_id)
        if existing and existing in self.active_processes:
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "⚠️ Codex is already processing a task in this thread. "
                "Cancelling the previous run...",
            )
            await self._terminate_process(existing)
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "⏹ Previous Codex task cancelled. Starting the new request...",
            )
        resume_id = self.settings_manager.get_agent_session_id(
            request.settings_key,
            request.base_session_id,
            agent_name=self.name,
        )

        if not os.path.exists(request.working_path):
            os.makedirs(request.working_path, exist_ok=True)

        cmd = self._build_command(request, resume_id)
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=request.working_path,
                limit=STREAM_BUFFER_LIMIT,
                **({"preexec_fn": os.setsid} if hasattr(os, "setsid") else {}),
            )
        except FileNotFoundError:
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "❌ Codex CLI not found. Please install it or set CODEX_CLI_PATH.",
            )
            await self._remove_ack_reaction(request)
            return
        except Exception as e:
            logger.error(f"Failed to launch Codex CLI: {e}", exc_info=True)
            await self.controller.emit_agent_message(
                request.context, "notify", f"❌ Failed to start Codex CLI: {e}"
            )
            await self._remove_ack_reaction(request)
            return

        await self._delete_ack(request)

        self.active_processes[request.composite_session_id] = (
            process,
            request.settings_key,
        )
        self.base_process_index[request.base_session_id] = request.composite_session_id
        self.composite_to_base[request.composite_session_id] = request.base_session_id
        logger.info(
            f"Codex session {request.composite_session_id} started (pid={process.pid})"
        )

        stdout_task = asyncio.create_task(self._consume_stdout(process, request))
        stderr_task = asyncio.create_task(self._consume_stderr(process, request))

        try:
            await process.wait()
            await asyncio.gather(stdout_task, stderr_task)
        finally:
            self._unregister_process(request.composite_session_id)
            # Clean up reaction as fallback if turn.completed was never received
            await self._remove_ack_reaction(request)

        if process.returncode != 0:
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "⚠️ Codex exited with a non-zero status. Review stderr for details.",
            )

    async def clear_sessions(self, settings_key: str) -> int:
        self.settings_manager.clear_agent_sessions(settings_key, self.name)
        # Terminate any active processes scoped to this settings key
        terminated = 0
        for key, (_, stored_key) in list(self.active_processes.items()):
            if stored_key == settings_key:
                await self._terminate_process(key)
                terminated += 1
        return terminated

    async def handle_stop(self, request: AgentRequest) -> bool:
        key = request.composite_session_id
        if not await self._terminate_process(key):
            key = self.base_process_index.get(request.base_session_id)
            if not key or not await self._terminate_process(key):
                return False
        await self.controller.emit_agent_message(
            request.context, "notify", "🛑 Terminated Codex execution."
        )
        logger.info(f"Codex session {key} terminated via /stop")
        return True

    def _unregister_process(self, composite_key: str):
        self.active_processes.pop(composite_key, None)
        self._pending_assistant_messages.pop(composite_key, None)
        base_id = self.composite_to_base.pop(composite_key, None)
        if base_id and self.base_process_index.get(base_id) == composite_key:
            self.base_process_index.pop(base_id, None)

    async def _terminate_process(self, composite_key: str) -> bool:
        entry = self.active_processes.get(composite_key)
        if not entry:
            return False

        proc, _ = entry
        try:
            if hasattr(os, "getpgid"):
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass

        self._unregister_process(composite_key)
        return True

    def _build_command(self, request: AgentRequest, resume_id: Optional[str]) -> list:
        cmd = [self.codex_config.binary, "exec", "--json"]
        cmd += ["--dangerously-bypass-approvals-and-sandbox"]
        cmd += ["--skip-git-repo-check"]

        if self.codex_config.default_model:
            cmd += ["--model", self.codex_config.default_model]

        cmd += ["--cd", request.working_path]
        cmd += self.codex_config.extra_args

        if resume_id:
            cmd += ["resume", resume_id]

        cmd.append(request.message)

        logger.info(f"Executing Codex command: {' '.join(cmd[:-1])} <prompt>")
        return cmd

    async def _consume_stdout(self, process: Process, request: AgentRequest):
        assert process.stdout is not None
        try:
            while True:
                try:
                    line = await process.stdout.readline()
                except (asyncio.LimitOverrunError, ValueError) as err:
                    await self._notify_stream_error(
                        request, f"Codex output too long; stream decode failed: {err}"
                    )
                    logger.exception("Codex stdout exceeded buffer limit")
                    break
                if not line:
                    break
                line = line.decode().strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(f"Codex emitted non-JSON line: {line}")
                    continue
                await self._handle_event(event, request)
        except Exception as err:
            await self._notify_stream_error(request, f"Codex stdout 读取异常：{err}")
            logger.exception("Unexpected Codex stdout error")

    async def _consume_stderr(self, process: Process, request: AgentRequest):
        assert process.stderr is not None
        buffer = []
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            decoded = line.decode(errors="ignore").rstrip()
            buffer.append(decoded)
            logger.debug(f"Codex stderr: {decoded}")

        if buffer:
            joined = "\n".join(buffer[-10:])
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                f"❗️ Codex stderr:\n```stderr\n{joined}\n```",
                parse_mode="markdown",
            )

    async def _handle_event(self, event: Dict, request: AgentRequest):
        event_type = event.get("type")

        if event_type == "thread.started":
            thread_id = event.get("thread_id")
            if thread_id:
                self.settings_manager.set_agent_session_mapping(
                    request.settings_key,
                    self.name,
                    request.base_session_id,
                    thread_id,
                )
            session_key = request.composite_session_id
            if session_key not in self._initialized_sessions:
                self._initialized_sessions.add(session_key)
                system_text = self.im_client.formatter.format_system_message(
                    request.working_path, "init", thread_id
                )
                await self.controller.emit_agent_message(
                    request.context,
                    "system",
                    system_text,
                    parse_mode="markdown",
                )
            return

        if event_type == "item.completed":
            details = event.get("item", {})
            item_type = details.get("type")

            if item_type == "agent_message":
                text = details.get("text", "")
                if text:
                    session_key = request.composite_session_id
                    pending = self._pending_assistant_messages.get(session_key)
                    if pending:
                        pending_text, pending_parse_mode = pending
                        await self.controller.emit_agent_message(
                            request.context,
                            "assistant",
                            pending_text,
                            parse_mode=pending_parse_mode or "markdown",
                        )

                    self._pending_assistant_messages[session_key] = (
                        self._prepare_last_message_payload(text)
                    )
            elif item_type == "command_execution":
                command = details.get("command")
                status = details.get("status")
                if command:
                    toolcall = self.im_client.formatter.format_toolcall(
                        "bash",
                        {"command": command, "status": status},
                    )
                    await self.controller.emit_agent_message(
                        request.context,
                        "toolcall",
                        toolcall,
                        parse_mode="markdown",
                    )
            elif item_type == "reasoning":
                text = details.get("text", "")
                if text:
                    await self.controller.emit_agent_message(
                        request.context,
                        "assistant",
                        f"_🧠 {text}_",
                        parse_mode="markdown",
                    )
            return

        if event_type == "error":
            message = event.get("message", "Unknown error")
            await self.controller.emit_agent_message(
                request.context, "notify", f"❌ Codex error: {message}"
            )
            return

        if event_type == "turn.failed":
            error = event.get("error", {}).get("message", "Turn failed.")
            await self.controller.emit_agent_message(
                request.context, "notify", f"⚠️ Codex turn failed: {error}"
            )
            self._pending_assistant_messages.pop(request.composite_session_id, None)
            return

        if event_type == "turn.completed":
            pending = self._pending_assistant_messages.pop(
                request.composite_session_id, None
            )
            if pending:
                pending_text, pending_parse_mode = pending
                await self.emit_result_message(
                    request.context,
                    pending_text,
                    subtype="success",
                    started_at=request.started_at,
                    parse_mode=pending_parse_mode or "markdown",
                    request=request,
                )
            else:
                await self.emit_result_message(
                    request.context,
                    None,
                    subtype="success",
                    started_at=request.started_at,
                    parse_mode="markdown",
                    request=request,
                )
            return

    async def _delete_ack(self, request: AgentRequest):
        ack_id = request.ack_message_id
        if ack_id and hasattr(self.im_client, "delete_message"):
            try:
                await self.im_client.delete_message(request.context.channel_id, ack_id)
            except Exception as err:
                logger.debug(f"Could not delete ack message: {err}")
            finally:
                request.ack_message_id = None

    async def _remove_ack_reaction(self, request: AgentRequest) -> None:
        """Remove acknowledgement reaction on error paths."""
        if request.ack_reaction_message_id and request.ack_reaction_emoji:
            try:
                await self.im_client.remove_reaction(
                    request.context,
                    request.ack_reaction_message_id,
                    request.ack_reaction_emoji,
                )
            except Exception as err:
                logger.debug(f"Could not remove ack reaction: {err}")
            finally:
                request.ack_reaction_message_id = None
                request.ack_reaction_emoji = None

    def _prepare_last_message_payload(self, text: str) -> Tuple[str, Optional[str]]:
        """Prepare cached assistant text for reuse in result messages."""
        return text, "markdown"

    async def _notify_stream_error(self, request: AgentRequest, message: str) -> None:
        """Emit a notify message when Codex stdout handling fails."""
        await self.controller.emit_agent_message(
            request.context,
            "notify",
            f"⚠️ {message}\n请查看 `~/.vibe_remote/logs/vibe_remote.log` 获取更多细节。",
        )
