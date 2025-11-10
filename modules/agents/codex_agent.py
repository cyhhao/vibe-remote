import asyncio
import json
import logging
import os
import signal
from asyncio.subprocess import Process
from typing import Dict, Optional, Tuple

from modules.agents.base import AgentRequest, BaseAgent

logger = logging.getLogger(__name__)


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
            request.working_path,
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
                **({"preexec_fn": os.setsid} if hasattr(os, "setsid") else {}),
            )
        except FileNotFoundError:
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "❌ Codex CLI not found. Please install it or set CODEX_CLI_PATH.",
            )
            return
        except Exception as e:
            logger.error(f"Failed to launch Codex CLI: {e}", exc_info=True)
            await self.controller.emit_agent_message(
                request.context, "notify", f"❌ Failed to start Codex CLI: {e}"
            )
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

        stdout_task = asyncio.create_task(
            self._consume_stdout(process, request)
        )
        stderr_task = asyncio.create_task(
            self._consume_stderr(process, request)
        )

        try:
            await process.wait()
            await asyncio.gather(stdout_task, stderr_task)
        finally:
            self._unregister_process(request.composite_session_id)

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
        while True:
            line = await process.stdout.readline()
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
                "system",
                f"```stderr\n{joined}\n```",
                parse_mode="markdown",
            )

    async def _handle_event(
        self, event: Dict, request: AgentRequest
    ):
        event_type = event.get("type")

        if event_type == "thread.started":
            thread_id = event.get("thread_id")
            if thread_id:
                self.settings_manager.set_agent_session_mapping(
                    request.settings_key,
                    self.name,
                    request.base_session_id,
                    request.working_path,
                    thread_id,
                )
            session_key = request.composite_session_id
            if session_key not in self._initialized_sessions:
                self._initialized_sessions.add(session_key)
                system_text = (
                    ":wrench: System init\n"
                    f":file_folder: Working directory: {request.working_path}\n"
                    f":link: Session ID: {thread_id}\n"
                    ":sparkles: Ready to work!\n---"
                )
                await self.controller.emit_agent_message(
                    request.context, "system", system_text, parse_mode="markdown"
                )
            return

        if event_type == "item.completed":
            details = event.get("item", {})
            item_type = details.get("type")
            if item_type == "agent_message":
                text = details.get("text", "")
                if text:
                    await self.controller.emit_agent_message(
                        request.context, "assistant", text, parse_mode="markdown"
                    )
                    request.last_agent_message = text
            elif item_type == "command_execution":
                command = details.get("command")
                output = details.get("aggregated_output", "")
                status = details.get("status")
                message_parts = [f"🛠️ `{command}` → {status}"]
                if output:
                    snippet = output[-2000:]
                    message_parts.append(f"```shell\n{snippet}\n```")
                await self.controller.emit_agent_message(
                    request.context,
                    "assistant",
                    "\n".join(message_parts),
                    parse_mode="markdown",
                )
            elif item_type == "reasoning":
                text = details.get("text", "")
                if text:
                    await self.controller.emit_agent_message(
                        request.context,
                        "response",
                        f"_🧠 {text}_",
                        parse_mode="markdown",
                    )
            return

        if event_type == "error":
            message = event.get("message", "Unknown error")
            await self.controller.emit_agent_message(
                request.context, "system", f"❌ Codex error: {message}"
            )
            return

        if event_type == "turn.failed":
            error = event.get("error", {}).get("message", "Turn failed.")
            await self.controller.emit_agent_message(
                request.context, "system", f"⚠️ Codex turn failed: {error}"
            )
            request.last_agent_message = None
            return

        if event_type == "turn.completed":
            if request.last_agent_message:
                await self.controller.emit_agent_message(
                    request.context,
                    "result",
                    request.last_agent_message,
                    parse_mode="markdown",
                )
                request.last_agent_message = None
            return

    async def _delete_ack(self, request: AgentRequest):
        ack_id = request.ack_message_id
        if ack_id and hasattr(self.im_client, "delete_message"):
            try:
                await self.im_client.delete_message(
                    request.context.channel_id, ack_id
                )
            except Exception as err:
                logger.debug(f"Could not delete ack message: {err}")
            finally:
                request.ack_message_id = None
