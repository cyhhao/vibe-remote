"""Unified polling loop for OpenCode sessions."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from modules.agents.base import AgentRequest

from .question_handler import OpenCodeQuestionHandler
from .server import OpenCodeServerManager

logger = logging.getLogger(__name__)


class OpenCodePollLoop:
    def __init__(self, agent, question_handler: OpenCodeQuestionHandler):
        self._agent = agent
        self._question_handler = question_handler

    async def run_prompt_poll(
        self,
        request: AgentRequest,
        server: OpenCodeServerManager,
        session_id: str,
        *,
        agent_to_use: Optional[str],
        model_dict: Optional[Dict[str, str]],
        reasoning_effort: Optional[str],
        baseline_message_ids: set[str],
    ) -> tuple[Optional[str], bool]:
        """Poll messages for a prompt.

        Returns:
            (final_text, should_emit_final_result)

        If `should_emit_final_result` is False, the caller should exit without
        emitting a final result message (e.g. timed out waiting for question).
        """

        seen_tool_calls: set[str] = set()
        emitted_assistant_messages: set[str] = set()
        poll_interval_seconds = 2.0
        final_text: Optional[str] = None

        error_retry_count = 0
        error_retry_limit = getattr(self._agent.opencode_config, "error_retry_limit", 1)
        last_error_message_id: Optional[str] = None

        def _relative_path(path: str) -> str:
            return self._agent._to_relative_path(path, request.working_path)

        poll_iter = 0
        while True:
            poll_iter += 1
            restart_poll = False
            try:
                messages = await server.list_messages(
                    session_id=session_id,
                    directory=request.working_path,
                )
                if poll_iter % 5 == 0:
                    last_info = messages[-1].get("info", {}) if messages else {}
                    logger.info(
                        "OpenCode poll heartbeat %s iter=%s last=%s role=%s completed=%s finish=%s error=%s",
                        session_id,
                        poll_iter,
                        last_info.get("id"),
                        last_info.get("role"),
                        bool(last_info.get("time", {}).get("completed")),
                        last_info.get("finish"),
                        bool(last_info.get("error")),
                    )
            except Exception as poll_err:
                logger.warning(f"Failed to poll OpenCode messages: {poll_err}")
                await asyncio.sleep(poll_interval_seconds)
                continue

            for message in messages:
                info = message.get("info", {})
                message_id = info.get("id")
                if not message_id or message_id in baseline_message_ids:
                    continue
                if info.get("role") != "assistant":
                    continue

                for part in message.get("parts", []) or []:
                    if part.get("type") != "tool":
                        continue
                    call_key = part.get("callID") or part.get("id")
                    if not call_key or call_key in seen_tool_calls:
                        continue
                    tool_name = part.get("tool") or "tool"
                    tool_state = part.get("state") or {}
                    tool_input = tool_state.get("input") or {}

                    if (
                        tool_name == "question"
                        and tool_state.get("status") != "completed"
                    ):
                        answered = (
                            await self._question_handler.handle_question_toolcall(
                                request=request,
                                server=server,
                                opencode_session_id=session_id,
                                message_id=message_id,
                                tool_part=part,
                                tool_input=tool_input,
                                call_key=call_key,
                                seen_tool_calls=seen_tool_calls,
                            )
                        )
                        if answered:
                            restart_poll = True
                            break
                        # Timeout -> end request without final result message
                        return None, False

                    toolcall = self._agent.im_client.formatter.format_toolcall(
                        tool_name,
                        tool_input,
                        get_relative_path=_relative_path,
                    )
                    await self._agent.controller.emit_agent_message(
                        request.context,
                        "toolcall",
                        toolcall,
                        parse_mode="markdown",
                    )
                    seen_tool_calls.add(call_key)

                if restart_poll:
                    break

                if (
                    info.get("time", {}).get("completed")
                    and message_id not in emitted_assistant_messages
                    and info.get("finish") == "tool-calls"
                ):
                    text = self._agent._extract_response_text(message)
                    if text:
                        await self._agent.controller.emit_agent_message(
                            request.context,
                            "assistant",
                            text,
                            parse_mode="markdown",
                        )
                    emitted_assistant_messages.add(message_id)

            if restart_poll:
                logger.info(
                    "Restarting poll loop for %s after question answer", session_id
                )
                continue

            if messages:
                last_message = messages[-1]
                last_info = last_message.get("info", {})
                last_id = last_info.get("id")

                if (
                    last_id
                    and last_id not in baseline_message_ids
                    and last_info.get("role") == "assistant"
                    and last_info.get("time", {}).get("completed")
                ):
                    msg_error = last_info.get("error")
                    if msg_error and last_id != last_error_message_id:
                        last_error_message_id = last_id
                        error_name = msg_error.get("name", "UnknownError")
                        error_data = msg_error.get("data", {})
                        error_msg = (
                            error_data.get("message", "")
                            if isinstance(error_data, dict)
                            else str(error_data)
                        )

                        logger.warning(
                            "OpenCode message error detected for %s: %s - %s (retry %d/%d)",
                            session_id,
                            error_name,
                            error_msg[:200],
                            error_retry_count,
                            error_retry_limit,
                        )

                        if error_retry_count < error_retry_limit:
                            error_retry_count += 1
                            logger.info(
                                "Auto-retrying OpenCode session %s with 'continue' (attempt %d/%d)",
                                session_id,
                                error_retry_count,
                                error_retry_limit,
                            )

                            try:
                                await server.prompt_async(
                                    session_id=session_id,
                                    directory=request.working_path,
                                    text="continue",
                                    agent=agent_to_use,
                                    model=model_dict,
                                    reasoning_effort=reasoning_effort,
                                )
                                await asyncio.sleep(poll_interval_seconds)
                                continue
                            except Exception as retry_err:
                                logger.error(
                                    "Failed to send retry 'continue' for %s: %s",
                                    session_id,
                                    retry_err,
                                )

                        await self._agent.controller.emit_agent_message(
                            request.context,
                            "notify",
                            f"OpenCode error: {error_name} - {error_msg[:500]}",
                        )
                        final_text = None
                        break

                    if last_info.get("finish") != "tool-calls":
                        if not msg_error:
                            error_retry_count = 0
                        final_text = self._agent._extract_response_text(last_message)
                        break

            await asyncio.sleep(poll_interval_seconds)

        return final_text, True

    async def run_restored_poll_loop(self, poll_info) -> None:
        """Continue a poll loop that was interrupted by restart."""

        from modules.im import MessageContext

        session_id = poll_info.opencode_session_id
        context = MessageContext(
            user_id="",
            channel_id=poll_info.channel_id,
            thread_id=poll_info.thread_id,
        )

        async def _remove_ack_reaction() -> None:
            """Remove ack reaction from the original message."""
            if poll_info.ack_reaction_message_id and poll_info.ack_reaction_emoji:
                try:
                    await self._agent.im_client.remove_reaction(
                        context,
                        poll_info.ack_reaction_message_id,
                        poll_info.ack_reaction_emoji,
                    )
                except Exception as e:
                    logger.debug(f"Failed to remove ack reaction: {e}")

        await self._agent.controller.emit_agent_message(
            context,
            "notify",
            "Resuming interrupted OpenCode session after restart...",
        )

        server = await self._agent._get_server()
        baseline_message_ids = set(poll_info.baseline_message_ids)
        seen_tool_calls = set(poll_info.seen_tool_calls)
        emitted_assistant_messages = set(poll_info.emitted_assistant_messages)
        poll_interval_seconds = 2.0
        final_text: Optional[str] = None

        error_retry_count = 0
        error_retry_limit = getattr(self._agent.opencode_config, "error_retry_limit", 1)
        last_error_message_id: Optional[str] = None

        started_at = time.monotonic()

        def _relative_path(path: str) -> str:
            return self._agent._to_relative_path(path, poll_info.working_path)

        try:
            poll_iter = 0
            while True:
                poll_iter += 1
                try:
                    messages = await server.list_messages(
                        session_id=session_id,
                        directory=poll_info.working_path,
                    )
                    if poll_iter % 5 == 0:
                        last_info = messages[-1].get("info", {}) if messages else {}
                        logger.info(
                            "OpenCode restored poll heartbeat %s iter=%s last=%s role=%s completed=%s finish=%s error=%s",
                            session_id,
                            poll_iter,
                            last_info.get("id"),
                            last_info.get("role"),
                            bool(last_info.get("time", {}).get("completed")),
                            last_info.get("finish"),
                            bool(last_info.get("error")),
                        )
                except Exception as poll_err:
                    logger.warning(
                        f"Failed to poll OpenCode messages (restored): {poll_err}"
                    )
                    await asyncio.sleep(poll_interval_seconds)
                    continue

                for message in messages:
                    info = message.get("info", {})
                    message_id = info.get("id")
                    if not message_id or message_id in baseline_message_ids:
                        continue
                    if info.get("role") != "assistant":
                        continue

                    for part in message.get("parts", []) or []:
                        if part.get("type") != "tool":
                            continue
                        call_key = part.get("callID") or part.get("id")
                        if not call_key or call_key in seen_tool_calls:
                            continue
                        tool_name = part.get("tool") or "tool"
                        tool_state = part.get("state") or {}
                        tool_input = tool_state.get("input") or {}

                        if (
                            tool_name == "question"
                            and tool_state.get("status") != "completed"
                        ):
                            logger.info(
                                "Detected question in restored poll for %s, exiting poll loop",
                                session_id,
                            )
                            self._agent.settings_manager.remove_active_poll(session_id)
                            await _remove_ack_reaction()
                            await self._agent.controller.emit_agent_message(
                                context,
                                "notify",
                                "OpenCode is waiting for input. Please check the session.",
                            )
                            return

                        seen_tool_calls.add(call_key)

                        poll_info.seen_tool_calls = list(seen_tool_calls)
                        self._agent.settings_manager.update_active_poll_state(
                            session_id, seen_tool_calls=poll_info.seen_tool_calls
                        )

                        if tool_name in (
                            "read",
                            "write",
                            "edit",
                            "bash",
                            "glob",
                            "grep",
                        ):
                            tool_summary = f"`{tool_name}`"
                            if tool_name == "bash":
                                cmd = tool_input.get("command", "")
                                if cmd:
                                    cmd_preview = (
                                        cmd[:50] + "..." if len(cmd) > 50 else cmd
                                    )
                                    tool_summary = f"`bash`: `{cmd_preview}`"
                            elif tool_name in ("read", "write", "edit"):
                                path = tool_input.get("file_path") or tool_input.get(
                                    "path", ""
                                )
                                if path:
                                    tool_summary = (
                                        f"`{tool_name}`: `{_relative_path(path)}`"
                                    )

                            await self._agent.controller.emit_agent_message(
                                context, "tool_call", tool_summary
                            )

                if messages:
                    last_message = messages[-1]
                    last_info = last_message.get("info", {})
                    if last_info.get("role") == "assistant":
                        time_info = last_info.get("time") or {}
                        if time_info.get("completed"):
                            msg_error = last_info.get("error")
                            if msg_error:
                                error_text = str(msg_error)
                                if last_info.get("id") != last_error_message_id:
                                    error_retry_count = 0
                                    last_error_message_id = last_info.get("id")
                                error_retry_count += 1
                                if error_retry_count > error_retry_limit:
                                    await self._agent.controller.emit_agent_message(
                                        context,
                                        "notify",
                                        f"OpenCode error: {error_text}",
                                    )
                                    self._agent.settings_manager.remove_active_poll(
                                        session_id
                                    )
                                    await _remove_ack_reaction()
                                    return

                            if last_info.get("finish") != "tool-calls":
                                if not msg_error:
                                    error_retry_count = 0
                                final_text = self._agent._extract_response_text(
                                    last_message
                                )
                                break

                await asyncio.sleep(poll_interval_seconds)

            if final_text:
                await self._agent.emit_result_message(
                    context,
                    final_text,
                    subtype="success",
                    started_at=started_at,
                    parse_mode="markdown",
                )
            else:
                await self._agent.emit_result_message(
                    context,
                    "(No response from OpenCode)",
                    subtype="warning",
                    started_at=started_at,
                )

            # Clean up ack reaction after result is sent
            await _remove_ack_reaction()
            # Clean up answer reaction after result is sent
            await self._question_handler.clear(poll_info.base_session_id)
            self._agent.settings_manager.remove_active_poll(session_id)

        except asyncio.CancelledError:
            logger.info(
                f"Restored OpenCode poll cancelled for {poll_info.base_session_id}"
            )
            await _remove_ack_reaction()
            await self._question_handler.clear(poll_info.base_session_id)
            self._agent.settings_manager.remove_active_poll(session_id)
            raise
        except Exception as e:
            error_name = type(e).__name__
            error_details = str(e).strip()
            error_text = (
                f"{error_name}: {error_details}" if error_details else error_name
            )

            logger.error(f"Restored OpenCode poll failed: {error_text}", exc_info=True)
            try:
                await server.abort_session(session_id, poll_info.working_path)
            except Exception as abort_err:
                logger.warning(
                    f"Failed to abort OpenCode session after error: {abort_err}"
                )

            self._agent.settings_manager.remove_active_poll(session_id)
            await _remove_ack_reaction()

            await self._agent.controller.emit_agent_message(
                context,
                "notify",
                f"Restored OpenCode session failed: {error_text}",
            )
