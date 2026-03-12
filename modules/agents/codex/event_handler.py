"""Translates Codex app-server notifications into vibe-remote agent messages."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional, Tuple

if TYPE_CHECKING:
    from modules.agents.base import AgentRequest

logger = logging.getLogger(__name__)


class CodexEventHandler:
    """Maps codex app-server server notifications to ``emit_agent_message`` calls.

    Maintains a *pending assistant message* buffer so that intermediate
    ``agent_message`` items are emitted immediately while the final one is held
    back until ``turn/completed`` and emitted as the result message.
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        # turnId → (accumulated_text, parse_mode)
        self._pending_assistant: dict[str, Tuple[str, Optional[str]]] = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def handle_notification(
        self,
        method: str,
        params: dict[str, Any],
        request: AgentRequest,
    ) -> None:
        handler = self._DISPATCH.get(method)
        if handler:
            await handler(self, params, request)
        else:
            logger.debug("Unhandled Codex notification: %s", method)

    # ------------------------------------------------------------------
    # Notification handlers
    # ------------------------------------------------------------------

    async def _on_thread_started(self, params: dict[str, Any], request: AgentRequest) -> None:
        thread_id = params.get("threadId", "")
        if thread_id:
            self._agent._session_mgr.set_thread_id(request.base_session_id, thread_id)
            # Persist thread_id in settings_manager for /clear resume
            self._agent.settings_manager.set_agent_session_mapping(
                request.settings_key,
                self._agent.name,
                request.base_session_id,
                thread_id,
            )

        system_text = self._agent.im_client.formatter.format_system_message(
            request.working_path,
            "init",
            thread_id,
        )
        await self._agent.controller.emit_agent_message(
            request.context,
            "system",
            system_text,
            parse_mode="markdown",
        )

    async def _on_turn_started(self, params: dict[str, Any], request: AgentRequest) -> None:
        turn_id = params.get("turnId", "")
        if turn_id:
            self._agent._session_mgr.set_active_turn(request.base_session_id, turn_id)
        logger.info(
            "Codex turn started: thread=%s turn=%s",
            params.get("threadId"),
            turn_id,
        )

    async def _on_turn_completed(self, params: dict[str, Any], request: AgentRequest) -> None:
        turn_id = params.get("turnId", "")
        self._agent._session_mgr.clear_active_turn(request.base_session_id)

        pending = self._pending_assistant.pop(turn_id, None)
        if pending:
            pending_text, pending_parse_mode = pending
            await self._agent.emit_result_message(
                request.context,
                pending_text,
                subtype="success",
                started_at=request.started_at,
                parse_mode=pending_parse_mode or "markdown",
                request=request,
            )
        else:
            await self._agent.emit_result_message(
                request.context,
                None,
                subtype="success",
                started_at=request.started_at,
                parse_mode="markdown",
                request=request,
            )

    async def _on_item_completed(self, params: dict[str, Any], request: AgentRequest) -> None:
        item = params.get("item", {})
        item_type = item.get("type")
        turn_id = params.get("turnId", "")

        if item_type == "agent_message":
            text = ""
            # Extract text from content array
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "Text":
                    text += content.get("text", "")
            # Fallback to top-level text field
            if not text:
                text = item.get("text", "")
            if text:
                # Emit previous pending message as assistant, buffer this one
                prev = self._pending_assistant.get(turn_id)
                if prev:
                    prev_text, prev_pm = prev
                    await self._agent.controller.emit_agent_message(
                        request.context,
                        "assistant",
                        prev_text,
                        parse_mode=prev_pm or "markdown",
                    )
                self._pending_assistant[turn_id] = (text, "markdown")

        elif item_type == "command_execution":
            command = item.get("command", "")
            status = item.get("status", "")
            exit_code = item.get("exitCode")
            output = item.get("output", "")
            if command:
                toolcall = self._agent.im_client.formatter.format_toolcall(
                    "bash",
                    {
                        "command": command,
                        "status": status,
                        "exit_code": exit_code,
                        "output": output[:500] if output else "",
                    },
                )
                await self._agent.controller.emit_agent_message(
                    request.context,
                    "toolcall",
                    toolcall,
                    parse_mode="markdown",
                )

        elif item_type == "file_change":
            file_path = item.get("filePath", "")
            change_type = item.get("changeType", "")
            if file_path:
                toolcall = self._agent.im_client.formatter.format_toolcall(
                    "file_change",
                    {"file": file_path, "type": change_type},
                )
                await self._agent.controller.emit_agent_message(
                    request.context,
                    "toolcall",
                    toolcall,
                    parse_mode="markdown",
                )

        elif item_type == "reasoning":
            text = item.get("text", "")
            # Try extracting from summary array
            if not text:
                for summary in item.get("summary", []):
                    if isinstance(summary, dict):
                        text += summary.get("text", "")
            if text:
                await self._agent.controller.emit_agent_message(
                    request.context,
                    "assistant",
                    f"_🧠 {text}_",
                    parse_mode="markdown",
                )

    async def _on_error(self, params: dict[str, Any], request: AgentRequest) -> None:
        error = params.get("error", {})
        message = error.get("message", "Unknown error") if isinstance(error, dict) else str(error)
        will_retry = params.get("willRetry", False)
        suffix = " (will retry)" if will_retry else ""
        await self._agent.controller.emit_agent_message(
            request.context,
            "notify",
            f"❌ Codex error: {message}{suffix}",
        )

    async def _on_agent_message_delta(self, params: dict[str, Any], request: AgentRequest) -> None:
        # Streaming delta — currently we accumulate at item/completed level,
        # but we could implement progressive Slack message updates here.
        pass

    async def _on_command_output_delta(self, params: dict[str, Any], request: AgentRequest) -> None:
        # Streaming command output — could implement live output display.
        pass

    async def _on_reasoning_delta(self, params: dict[str, Any], request: AgentRequest) -> None:
        # Streaming reasoning — currently handled at item/completed level.
        pass

    async def _on_context_compacted(self, params: dict[str, Any], request: AgentRequest) -> None:
        await self._agent.controller.emit_agent_message(
            request.context,
            "notify",
            "🗜️ Codex context was compacted to free up token space.",
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def clear_pending(self, turn_id: str) -> None:
        """Discard buffered text for a turn (e.g. on interruption)."""
        self._pending_assistant.pop(turn_id, None)

    # ------------------------------------------------------------------
    # Dispatch table
    # ------------------------------------------------------------------

    _DISPATCH: dict[str, Any] = {
        "thread/started": _on_thread_started,
        "turn/started": _on_turn_started,
        "turn/completed": _on_turn_completed,
        "item/completed": _on_item_completed,
        "error": _on_error,
        "item/agentMessage/delta": _on_agent_message_delta,
        "item/commandExecution/outputDelta": _on_command_output_delta,
        "item/reasoning/summaryTextDelta": _on_reasoning_delta,
        "thread/compacted": _on_context_compacted,
    }
