"""Translates Codex app-server notifications into vibe-remote agent messages."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

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
        thread_obj = params.get("thread", {})
        thread_id = thread_obj.get("id", "") if isinstance(thread_obj, dict) else ""
        if thread_id:
            self._agent._session_mgr.set_thread_id(request.base_session_id, thread_id)
            # Persist thread_id in settings_manager for /clear resume
            self._agent.sessions.set_agent_session_mapping(
                request.session_key,
                self._agent.name,
                request.base_session_id,
                thread_id,
            )

        system_text = self._agent._get_formatter(request.context).format_system_message(
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
        turn_obj = params.get("turn", {})
        turn_id = turn_obj.get("id", "") if isinstance(turn_obj, dict) else ""
        logger.info(
            "Codex turn started: thread=%s turn=%s",
            params.get("threadId"),
            turn_id,
        )

    async def _on_turn_completed(self, params: dict[str, Any], request: AgentRequest) -> None:
        turn_obj = params.get("turn", {})
        turn_id = turn_obj.get("id", "") if isinstance(turn_obj, dict) else ""
        status = turn_obj.get("status", "") if isinstance(turn_obj, dict) else ""
        turn_state = self._agent._turn_registry.get_turn(turn_id)
        tracked_request = turn_state.request if turn_state else request
        should_emit_result = self._agent._turn_registry.should_emit_result(turn_id)
        should_emit_terminal_error = self._agent._turn_registry.should_emit_terminal_error(turn_id)

        if status == "interrupted":
            if not turn_state:
                logger.debug("Ignoring interrupted completion for unknown turn %s", turn_id)
                return
            self._agent._turn_registry.pop_turn(turn_id)
            await self._agent._remove_ack_reaction(tracked_request)
            return

        if status == "failed":
            if not turn_state:
                logger.info("Ignoring failed completion for unknown turn %s", turn_id)
                return
            error_msg = turn_state.terminal_error if turn_state else None
            already_notified = turn_state.terminal_error_notified if turn_state else False
            if not error_msg:
                error_obj = turn_obj.get("error", {}) if isinstance(turn_obj, dict) else {}
                error_msg = self._extract_error_message(error_obj)

            if should_emit_terminal_error and not already_notified:
                await self._agent.controller.emit_agent_message(
                    tracked_request.context,
                    "notify",
                    f"❌ Codex turn failed: {error_msg}",
                )
            else:
                logger.info("Suppressing inactive Codex turn failure for %s: %s", turn_id, error_msg)

            self._agent._turn_registry.pop_turn(turn_id)
            await self._agent._remove_ack_reaction(tracked_request)
            return

        if not should_emit_result:
            if not turn_state:
                logger.debug("Ignoring completion for unknown turn %s", turn_id)
                return
            self._agent._turn_registry.pop_turn(turn_id)
            logger.debug("Ignoring inactive turn/completed for turn %s", turn_id)
            await self._agent._remove_ack_reaction(tracked_request)
            return

        pending = turn_state.pending_assistant if turn_state else None
        self._agent._turn_registry.pop_turn(turn_id)
        if pending:
            pending_text, pending_parse_mode = pending
            await self._agent.emit_result_message(
                tracked_request.context,
                pending_text,
                subtype="success",
                started_at=tracked_request.started_at,
                parse_mode=pending_parse_mode or "markdown",
                request=tracked_request,
            )
        else:
            await self._agent.emit_result_message(
                tracked_request.context,
                None,
                subtype="success",
                started_at=tracked_request.started_at,
                parse_mode="markdown",
                request=tracked_request,
            )

    async def _on_item_completed(self, params: dict[str, Any], request: AgentRequest) -> None:
        item = params.get("item", {})
        item_type = item.get("type")
        turn_id = params.get("turnId", "")

        if turn_id and not self._agent._turn_registry.should_emit_progress(turn_id):
            logger.debug("Ignoring stale/interrupted item/%s for turn %s", item_type, turn_id)
            return

        turn_state = self._agent._turn_registry.get_turn(turn_id) if turn_id else None

        if item_type == "agentMessage":
            text = item.get("text", "")
            if text:
                # Emit previous pending message as assistant, buffer this one
                prev = turn_state.pending_assistant if turn_state else None
                if prev:
                    prev_text, prev_pm = prev
                    await self._agent.controller.emit_agent_message(
                        request.context,
                        "assistant",
                        prev_text,
                        parse_mode=prev_pm or "markdown",
                    )
                if turn_state:
                    turn_state.pending_assistant = (text, "markdown")

        elif item_type == "commandExecution":
            command = item.get("command", "")
            status = item.get("status", "")
            exit_code = item.get("exitCode")
            output = item.get("aggregatedOutput", "")
            if command:
                toolcall = self._agent._get_formatter(request.context).format_toolcall(
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

        elif item_type == "fileChange":
            changes = item.get("changes", [])
            for change in changes:
                if not isinstance(change, dict):
                    continue
                file_path = change.get("path", "")
                change_kind = change.get("kind", "")
                if file_path:
                    toolcall = self._agent._get_formatter(request.context).format_toolcall(
                        "file_change",
                        {"file": file_path, "type": change_kind},
                    )
                    await self._agent.controller.emit_agent_message(
                        request.context,
                        "toolcall",
                        toolcall,
                        parse_mode="markdown",
                    )

        elif item_type == "reasoning":
            # Extract from summary array (list of strings) or content array
            parts: list[str] = []
            for s in item.get("summary", []):
                if isinstance(s, str):
                    parts.append(s)
            if not parts:
                for c in item.get("content", []):
                    if isinstance(c, str):
                        parts.append(c)
            text = "\n".join(parts)
            if text:
                await self._agent.controller.emit_agent_message(
                    request.context,
                    "assistant",
                    f"_🧠 {text}_",
                    parse_mode="markdown",
                )

    async def _on_error(self, params: dict[str, Any], request: AgentRequest) -> None:
        error = params.get("error", {})
        message = self._extract_error_message(error)
        will_retry = params.get("willRetry") is True
        turn_id = params.get("turnId", "")

        if will_retry:
            logger.info("Suppressing transient Codex error for turn %s: %s", turn_id or "<unknown>", message)
            return

        if turn_id:
            turn_state = self._agent._turn_registry.get_turn(turn_id)
            if not turn_state:
                logger.info("Ignoring Codex error for unknown turn %s: %s", turn_id, message)
                return

            turn_state.terminal_error = message
            if (
                self._agent._turn_registry.should_emit_terminal_error(turn_id)
                and not turn_state.terminal_error_notified
            ):
                await self._agent.controller.emit_agent_message(
                    request.context,
                    "notify",
                    f"❌ Codex turn failed: {message}",
                )
                turn_state.terminal_error_notified = True
            else:
                logger.info("Logging inactive Codex turn error for %s: %s", turn_id, message)
            return

        await self._agent.controller.emit_agent_message(
            request.context,
            "notify",
            f"❌ Codex error: {message}",
        )

    def _extract_error_message(self, error: Any) -> str:
        if isinstance(error, dict):
            return error.get("message", "Unknown error")
        return str(error)

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

    def clear_pending(self, turn_id: str) -> AgentRequest | None:
        """Hide a turn from user-facing output after interruption/replacement."""
        turn_state = self._agent._turn_registry.hide_turn(turn_id)
        return turn_state.request if turn_state else None

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
