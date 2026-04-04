import asyncio
import logging
import os
from typing import Callable, Optional

from core.agent_auth_service import classify_auth_error
from modules.claude_sdk_compat import TextBlock, ToolUseBlock

from modules.agents.base import AgentRequest, BaseAgent

# NOTE: AskUserQuestion support is disabled because Claude Code SDK cannot
# respond to it programmatically. See: https://github.com/anthropics/claude-code/issues/10168
# Keeping the import for future use when SDK adds support.
# from modules.agents.claude_question_handler import ClaudeQuestionHandler
from modules.im import MessageContext

logger = logging.getLogger(__name__)


class ClaudeAgent(BaseAgent):
    """Existing Claude Code integration extracted into an agent backend."""

    name = "claude"

    # AskUserQuestion support is disabled - SDK cannot respond programmatically
    # Set to True when SDK adds support (see issue #10168)
    ENABLE_ASK_USER_QUESTION = False

    def __init__(self, controller):
        super().__init__(controller)
        self.session_handler = controller.session_handler
        self.session_manager = controller.session_manager
        self.receiver_tasks = controller.receiver_tasks
        self.claude_sessions = controller.claude_sessions
        self.claude_client = controller.claude_client
        self._last_assistant_text: dict[str, str] = {}
        self._pending_assistant_message: dict[str, str] = {}
        # Store reaction info per session as a queue (FIFO) for cleanup after result
        # Each entry is (reaction_message_id, emoji)
        self._pending_reactions: dict[str, list[tuple[str, str]]] = {}
        self._pending_requests: dict[str, list[AgentRequest]] = {}

        # Question handler for AskUserQuestion support (disabled)
        # NOTE: Uncomment when SDK adds AskUserQuestion support
        # self._question_handler = ClaudeQuestionHandler(
        #     agent=self,
        #     controller=controller,
        #     im_client=controller.im_client,
        #     settings_manager=controller.settings_manager,
        # )
        self._question_handler = None

    async def handle_message(self, request: AgentRequest) -> None:
        context = request.context

        # Question callback handling (disabled - SDK doesn't support AskUserQuestion response)
        # if self.ENABLE_ASK_USER_QUESTION and request.message.startswith("claude_question:"):
        #     await self._handle_question_callback(request)
        #     return

        try:
            client = await self.session_handler.get_or_create_claude_session(
                context,
                subagent_name=request.subagent_name,
                subagent_model=request.subagent_model,
                subagent_reasoning_effort=request.subagent_reasoning_effort,
            )

            # Queue reaction BEFORE sending query to avoid race condition where
            # a fast result arrives before the reaction is queued
            if request.ack_reaction_message_id and request.ack_reaction_emoji:
                if request.composite_session_id not in self._pending_reactions:
                    self._pending_reactions[request.composite_session_id] = []
                self._pending_reactions[request.composite_session_id].append(
                    (request.ack_reaction_message_id, request.ack_reaction_emoji)
                )
            self._pending_requests.setdefault(request.composite_session_id, []).append(request)

            # Prepare message with file attachment info if present
            message = self._prepare_message_with_files(request)

            await client.query(message, session_id=request.composite_session_id)
            logger.info(f"Sent message to Claude for session {request.composite_session_id}")

            await self._delete_ack(context, request)

            if (
                request.composite_session_id not in self.receiver_tasks
                or self.receiver_tasks[request.composite_session_id].done()
            ):
                self.receiver_tasks[request.composite_session_id] = asyncio.create_task(
                    self._receive_messages(client, request.base_session_id, request.working_path, context)
                )
        except Exception as e:
            logger.error(f"Error processing Claude message: {e}", exc_info=True)
            # Clean up the specific reaction for this request (not FIFO)
            await self._remove_specific_pending_reaction(request.composite_session_id, context, request)
            self._remove_pending_request(request.composite_session_id, request)
            await self._remove_ack_reaction(request)
            handled = await self.controller.agent_auth_service.maybe_emit_auth_recovery_message(
                context,
                "claude",
                f"❌ Claude error: {e}",
            )
            if not handled:
                await self.session_handler.handle_session_error(request.composite_session_id, context, e)
        finally:
            await self._delete_ack(context, request)

    async def _handle_question_callback(self, request: AgentRequest) -> None:
        """Handle question-related callbacks (button clicks, modal submissions).

        NOTE: This method is disabled because Claude Code SDK cannot respond to
        AskUserQuestion programmatically. See: https://github.com/anthropics/claude-code/issues/10168
        """
        # AskUserQuestion support disabled
        await self.controller.emit_agent_message(
            request.context,
            "notify",
            "AskUserQuestion support is currently disabled. Claude Code SDK does not support programmatic responses to this tool.",
        )
        return

    async def clear_sessions(self, session_key: str) -> int:
        """Clear Claude sessions scoped to the provided session key."""
        agent_map = self.sessions.list_agent_sessions(session_key, self.name)
        session_bases_to_clear = set(agent_map.keys())

        self.sessions.clear_agent_sessions(session_key, self.name)

        sessions_to_clear = []
        for composite_id in list(self.claude_sessions.keys()):
            base_part = composite_id.split(":")[0] if ":" in composite_id else composite_id
            if base_part in session_bases_to_clear:
                sessions_to_clear.append(composite_id)

        for composite_id in sessions_to_clear:
            try:
                client = self.claude_sessions[composite_id]
                if hasattr(client, "close"):
                    await client.close()
            except Exception as e:
                logger.warning(f"Error closing Claude session {composite_id}: {e}")
            finally:
                self.claude_sessions.pop(composite_id, None)
                receiver_task = self.receiver_tasks.pop(composite_id, None)
                if receiver_task is not None:
                    receiver_task.cancel()
                    try:
                        await receiver_task
                    except asyncio.CancelledError:
                        pass
                    except Exception as task_err:
                        logger.warning(f"Error stopping Claude receiver {composite_id}: {task_err}")

                self._last_assistant_text.pop(composite_id, None)
                self._pending_assistant_message.pop(composite_id, None)
                self._pending_reactions.pop(composite_id, None)
                self._pending_requests.pop(composite_id, None)

        # Legacy session manager cleanup (best-effort)
        await self.session_manager.clear_session(session_key)

        return len(sessions_to_clear) or len(session_bases_to_clear)

    async def refresh_auth_state(self) -> None:
        """Reconnect Claude runtime so future requests load fresh auth."""
        session_ids = set(self.claude_sessions.keys()) | set(self.receiver_tasks.keys())

        for composite_id in session_ids:
            receiver_task = self.receiver_tasks.pop(composite_id, None)
            if receiver_task is not None and not receiver_task.done():
                receiver_task.cancel()
                try:
                    await receiver_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning("Error stopping Claude receiver %s during auth refresh: %s", composite_id, exc)

            client = self.claude_sessions.pop(composite_id, None)
            if client is not None:
                try:
                    if hasattr(client, "disconnect"):
                        await client.disconnect()
                    elif hasattr(client, "close"):
                        await client.close()
                except Exception as exc:
                    logger.warning("Error disconnecting Claude session %s during auth refresh: %s", composite_id, exc)

            self._last_assistant_text.pop(composite_id, None)
            self._pending_assistant_message.pop(composite_id, None)
            self._pending_reactions.pop(composite_id, None)
            self._pending_requests.pop(composite_id, None)

        logger.info("Refreshed Claude auth state across %d runtime session(s)", len(session_ids))

    async def _cleanup_runtime_session(
        self,
        composite_key: str,
        *,
        current_receiver_task: asyncio.Task | None = None,
    ) -> None:
        """Drop Claude runtime state without canceling the current receiver task."""

        receiver_task = self.receiver_tasks.pop(composite_key, None)
        if (
            receiver_task is not None
            and receiver_task is not current_receiver_task
            and not receiver_task.done()
        ):
            receiver_task.cancel()
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error stopping Claude receiver %s during cleanup: %s", composite_key, exc)

        client = self.claude_sessions.pop(composite_key, None)
        if client is not None:
            try:
                if hasattr(client, "disconnect"):
                    await client.disconnect()
                elif hasattr(client, "close"):
                    await client.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error disconnecting Claude session %s during cleanup: %s", composite_key, exc)

        self._last_assistant_text.pop(composite_key, None)
        self._pending_assistant_message.pop(composite_key, None)
        self._pending_reactions.pop(composite_key, None)
        self._pending_requests.pop(composite_key, None)

    async def handle_stop(self, request: AgentRequest) -> bool:
        composite_key = request.composite_session_id
        if composite_key not in self.claude_sessions:
            return False

        client = self.claude_sessions[composite_key]
        await self.controller.emit_agent_message(request.context, "notify", "🛑 Interrupting Claude session...")
        try:
            if hasattr(client, "interrupt"):
                await client.interrupt()
                return True
            else:
                await self.controller.emit_agent_message(
                    request.context,
                    "notify",
                    "⚠️ This Claude session cannot be interrupted; consider /new.",
                )
                return False
        except Exception as err:
            logger.error(f"Failed to interrupt Claude session {composite_key}: {err}")
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "⚠️ Failed to interrupt Claude session. Please try /new.",
            )
            return False

    async def _receive_messages(
        self,
        client,
        base_session_id: str,
        working_path: str,
        context: MessageContext,
    ):
        """Receive messages from Claude SDK client."""
        try:
            session_key = self.controller._get_session_key(context)
            composite_key = f"{base_session_id}:{working_path}"

            # Build a request object for question handler
            request = AgentRequest(
                context=context,
                message="",
                working_path=working_path,
                base_session_id=base_session_id,
                composite_session_id=composite_key,
                session_key=session_key,
            )

            async for message in client.receive_messages():
                try:
                    claude_session_id = self._maybe_capture_session_id(message, base_session_id, session_key)
                    if claude_session_id:
                        logger.info(f"Captured Claude session id {claude_session_id} for {base_session_id}")

                    if self.claude_client._is_skip_message(message):
                        continue

                    message_type = self._detect_message_type(message)
                    formatter = self._get_formatter(context)

                    if message_type == "assistant":
                        toolcalls = []
                        text_parts = []
                        # AskUserQuestion detection disabled - SDK cannot respond
                        # ask_user_question_block = None

                        for block in getattr(message, "content", []) or []:
                            if isinstance(block, ToolUseBlock):
                                # AskUserQuestion handling disabled - tool is disallowed via ClaudeAgentOptions
                                # if self.ENABLE_ASK_USER_QUESTION and self._question_handler:
                                #     if self._question_handler.is_ask_user_question(block):
                                #         ask_user_question_block = block
                                #         continue

                                toolcalls.append(
                                    formatter.format_toolcall(
                                        block.name,
                                        block.input,
                                        get_relative_path=lambda path: self.get_relative_path(path, context),
                                    )
                                )
                            elif isinstance(block, TextBlock):
                                text = block.text.strip() if block.text else ""
                                if text:
                                    text_parts.append(text)

                        assistant_text = self._extract_text_blocks(message, context)
                        if await self._handle_auth_failure_result(
                            context,
                            composite_key,
                            "error" if self._is_auth_failure_assistant_message(message) else "",
                            assistant_text,
                        ):
                            pending_request = self._pop_pending_request(composite_key)
                            self._discard_pending_reaction(composite_key)
                            if pending_request:
                                await self._remove_ack_reaction(pending_request)
                            self._last_assistant_text.pop(composite_key, None)
                            self._pending_assistant_message.pop(composite_key, None)
                            continue
                        if assistant_text:
                            self._last_assistant_text[composite_key] = assistant_text

                        pending = self._pending_assistant_message.pop(composite_key, None)
                        if pending:
                            await self.controller.emit_agent_message(
                                context,
                                "assistant",
                                pending,
                                parse_mode="markdown",
                            )

                        for toolcall in toolcalls:
                            await self.controller.emit_agent_message(
                                context,
                                "toolcall",
                                toolcall,
                                parse_mode="markdown",
                            )

                        if text_parts:
                            formatted_assistant = formatter.format_assistant_message(text_parts)
                            self._pending_assistant_message[composite_key] = formatted_assistant

                        # AskUserQuestion handling disabled - SDK cannot respond programmatically
                        # See: https://github.com/anthropics/claude-code/issues/10168
                        # if self.ENABLE_ASK_USER_QUESTION and ask_user_question_block:
                        #     logger.info(
                        #         "Detected AskUserQuestion for session %s",
                        #         base_session_id,
                        #     )
                        #     answered = await self._question_handler.handle_ask_user_question(
                        #         request=request,
                        #         tool_use_block=ask_user_question_block,
                        #         client=client,
                        #         composite_session_id=composite_key,
                        #     )
                        #     if not answered:
                        #         logger.warning(
                        #             "AskUserQuestion timed out for session %s",
                        #             base_session_id,
                        #         )
                        #         return
                        #     logger.info(
                        #         "AskUserQuestion answered for session %s, continuing",
                        #         base_session_id,
                        #     )

                        continue

                    if message_type == "system":
                        formatted_message = self.claude_client.format_message(
                            message,
                            get_relative_path=lambda path: self.get_relative_path(path, context),
                            formatter=formatter,
                        )
                        if await self._handle_auth_failure_result(
                            context,
                            composite_key,
                            getattr(message, "subtype", "") or "",
                            formatted_message,
                        ):
                            continue
                        if formatted_message and formatted_message.strip():
                            await self.controller.emit_agent_message(
                                context,
                                "system",
                                formatted_message,
                                parse_mode="markdown",
                            )
                        continue

                    if message_type == "result":
                        self._pending_assistant_message.pop(composite_key, None)
                        result_text = getattr(message, "result", None)
                        if not result_text:
                            # ResultMessage had no text; use the last assistant
                            # text as a fallback so the user still sees output.
                            fallback = self._last_assistant_text.get(composite_key)
                            if fallback:
                                result_text = fallback

                        if await self._handle_auth_failure_result(
                            context,
                            composite_key,
                            getattr(message, "subtype", "") or "",
                            result_text,
                        ):
                            pending_request = self._pop_pending_request(composite_key)
                            self._discard_pending_reaction(composite_key)
                            if pending_request:
                                await self._remove_ack_reaction(pending_request)
                            self._last_assistant_text.pop(composite_key, None)
                            self._pending_assistant_message.pop(composite_key, None)
                            continue

                        # NOTE: The pending assistant message is intentionally
                        # NOT emitted here.  ResultMessage.result already
                        # contains the same text as the last AssistantMessage,
                        # so sending both would duplicate the content.

                        pending_request = self._pop_pending_request(composite_key)

                        await self.emit_result_message(
                            context,
                            result_text,
                            subtype=getattr(message, "subtype", "") or "",
                            duration_ms=getattr(message, "duration_ms", 0),
                            parse_mode="markdown",
                            request=pending_request,
                        )

                        self._discard_pending_reaction(composite_key)

                        self._last_assistant_text.pop(composite_key, None)
                        session = await self.session_manager.get_or_create_session(context.user_id, context.channel_id)
                        if session:
                            session.session_active[f"{base_session_id}:{working_path}"] = False
                        continue

                    # Ignore UserMessage/tool results; toolcalls are emitted from ToolUseBlock.
                    continue
                except Exception as e:
                    logger.error(f"Error processing message from Claude: {e}", exc_info=True)
                    continue
        except asyncio.CancelledError:
            # Receiver task was explicitly cancelled (e.g. /stop, /clear,
            # or a new message replacing the session).  Clean up reactions
            # because this receiver will never process another result.
            composite_key = f"{base_session_id}:{working_path}"
            logger.info("Claude receiver cancelled for session %s", composite_key)
            await self._clear_pending_reactions(composite_key, context)
            raise
        except Exception as e:
            composite_key = f"{base_session_id}:{working_path}"
            logger.error(
                f"Error in Claude receiver for session {composite_key}: {e}",
                exc_info=True,
            )
            # Clean up all pending reactions for this session on error —
            # the receiver is dead and won't process any more results.
            await self._clear_pending_reactions(composite_key, context)
            handled = await self.controller.agent_auth_service.maybe_emit_auth_recovery_message(
                context,
                "claude",
                f"❌ Claude error: {e}",
            )
            if not handled:
                await self.session_handler.handle_session_error(composite_key, context, e)
        # NOTE: no `finally` cleanup of pending reactions here.
        # When the receiver ends normally (stream exhausted after a result),
        # new messages may have already queued their reactions via
        # handle_message().  Blindly clearing them here would remove the
        # :eyes: for an in-flight request that hasn't produced a result yet.
        # The except blocks above handle the cancel/error cases; the
        # normal-result case is handled by _remove_pending_reaction()
        # inside the loop.

    async def _delete_ack(self, context: MessageContext, request: AgentRequest):
        ack_id = request.ack_message_id
        if ack_id and hasattr(self.im_client, "delete_message"):
            try:
                await self.im_client.delete_message(context.channel_id, ack_id)
            except Exception as err:
                logger.debug(f"Could not delete ack message: {err}")
            finally:
                request.ack_message_id = None

    async def _remove_pending_reaction(self, composite_key: str, context: MessageContext) -> None:
        """Remove the oldest stored reaction for a session after result is sent.

        Uses FIFO queue to handle multiple messages in the same session.
        """
        reactions = self._pending_reactions.get(composite_key)
        if reactions:
            # Pop the oldest reaction (FIFO)
            message_id, emoji = reactions.pop(0)
            # Clean up empty list
            if not reactions:
                self._pending_reactions.pop(composite_key, None)
            try:
                await self.im_client.remove_reaction(context, message_id, emoji)
            except Exception as err:
                logger.debug(f"Failed to remove reaction ack: {err}")

    def _discard_pending_reaction(self, composite_key: str) -> None:
        reactions = self._pending_reactions.get(composite_key)
        if not reactions:
            return
        reactions.pop(0)
        if not reactions:
            self._pending_reactions.pop(composite_key, None)

    def _pop_pending_request(self, composite_key: str) -> Optional[AgentRequest]:
        requests = self._pending_requests.get(composite_key)
        if not requests:
            return None
        request = requests.pop(0)
        if not requests:
            self._pending_requests.pop(composite_key, None)
        return request

    def _remove_pending_request(self, composite_key: str, request: AgentRequest) -> None:
        requests = self._pending_requests.get(composite_key)
        if not requests:
            return
        for index, pending_request in enumerate(requests):
            if pending_request is request:
                requests.pop(index)
                break
        if not requests:
            self._pending_requests.pop(composite_key, None)

    async def _remove_specific_pending_reaction(
        self, composite_key: str, context: MessageContext, request: AgentRequest
    ) -> None:
        """Remove a specific reaction from the queue by matching message_id.

        Used on error paths to remove the current request's reaction instead of FIFO.
        """
        if not request.ack_reaction_message_id:
            return
        reactions = self._pending_reactions.get(composite_key)
        if not reactions:
            return
        # Find and remove the matching reaction
        target_id = request.ack_reaction_message_id
        target_emoji = request.ack_reaction_emoji
        for i, (msg_id, emoji) in enumerate(reactions):
            if msg_id == target_id and emoji == target_emoji:
                reactions.pop(i)
                if not reactions:
                    self._pending_reactions.pop(composite_key, None)
                try:
                    await self.im_client.remove_reaction(context, msg_id, emoji)
                except Exception as err:
                    logger.debug(f"Failed to remove reaction ack: {err}")
                return

    async def _clear_pending_reactions(self, composite_key: str, context: MessageContext) -> None:
        """Clear all pending reactions for a session (for error cleanup)."""
        reactions = self._pending_reactions.pop(composite_key, None)
        requests = self._pending_requests.pop(composite_key, None)
        if reactions:
            for message_id, emoji in reactions:
                try:
                    await self.im_client.remove_reaction(context, message_id, emoji)
                except Exception as err:
                    logger.debug(f"Failed to remove reaction ack: {err}")
        if requests:
            for request in requests:
                await self._remove_ack_reaction(request)

    def get_relative_path(self, abs_path: str, context: Optional[MessageContext] = None) -> str:
        """Convert absolute path to relative path from working directory."""
        try:
            cwd = self.session_handler.get_working_path(context)
            abs_path = os.path.abspath(os.path.expanduser(abs_path))
            rel_path = os.path.relpath(abs_path, cwd)
            if rel_path.startswith("../.."):
                return abs_path
            return rel_path
        except Exception:
            return abs_path

    def _get_target_context(self, context: MessageContext) -> MessageContext:
        """Return context for sending messages (respect Slack thread replies)."""
        if self.im_client.should_use_thread_for_reply() and context.thread_id:
            return MessageContext(
                user_id=context.user_id,
                channel_id=context.channel_id,
                thread_id=context.thread_id,
                message_id=context.message_id,
                platform_specific=context.platform_specific,
            )
        return context

    def _maybe_capture_session_id(
        self,
        message,
        base_session_id: str,
        session_key: str,
    ) -> Optional[str]:
        """Capture session id from system init messages."""
        if (
            hasattr(message, "__class__")
            and message.__class__.__name__ == "SystemMessage"
            and getattr(message, "subtype", None) == "init"
            and getattr(message, "data", None)
        ):
            session_id = message.data.get("session_id")
            if session_id:
                self.session_handler.capture_session_id(base_session_id, session_id, session_key)
                return session_id
        return None

    def _extract_text_blocks(self, message, context: MessageContext) -> str:
        """Extract text-only content blocks for result fallbacks."""
        parts = []
        for block in getattr(message, "content", []) or []:
            if isinstance(block, TextBlock):
                text = block.text.strip() if block.text else ""
                if text:
                    parts.append(self._get_formatter(context).escape_special_chars(text))
        return "\n\n".join(parts).strip()

    async def _handle_auth_failure_result(
        self,
        context: MessageContext,
        composite_key: str,
        subtype: str,
        text: Optional[str],
    ) -> bool:
        if not text or not text.strip():
            return False

        normalized_subtype = (subtype or "").strip().lower()
        if normalized_subtype not in {"error", "failed"}:
            return False

        if not classify_auth_error("claude", text):
            return False

        handled = await self.controller.agent_auth_service.maybe_emit_auth_recovery_message(
            context,
            "claude",
            f"❌ Claude error: {text}",
        )
        if handled:
            await self._cleanup_runtime_session(composite_key, current_receiver_task=asyncio.current_task())
        return handled

    def _is_auth_failure_assistant_message(self, message) -> bool:
        if not getattr(message, "isApiErrorMessage", False):
            return False

        error_kind = (getattr(message, "error", "") or "").strip().lower()
        return error_kind == "authentication_failed"

    def _detect_message_type(self, message) -> Optional[str]:
        """Infer message type name from Claude SDK class."""
        if not hasattr(message, "__class__"):
            return None
        class_name = message.__class__.__name__
        mapping = {
            "SystemMessage": "system",
            "UserMessage": "user",
            "AssistantMessage": "assistant",
            "ResultMessage": "result",
        }
        return mapping.get(class_name)

    def _prepare_message_with_files(self, request: AgentRequest) -> str:
        """Prepare message with file attachment information.

        If there are file attachments, append file info to the message
        so the agent knows what files are available to read.
        Files are stored in ~/.vibe_remote/attachments/{channel_id}/.

        Args:
            request: The agent request containing message and files

        Returns:
            Message string, potentially with file info appended
        """
        if not request.files:
            return request.message

        # Build file info section
        images = []
        other_files = []

        for attachment in request.files:
            if not attachment.local_path:
                continue

            is_image = (attachment.mimetype or "").startswith("image/")
            if is_image:
                images.append(attachment)
            else:
                other_files.append(attachment)

        if not images and not other_files:
            return request.message

        # Format file info as a clear block at the end
        file_lines = ["", "[User Attachments]"]

        for img in images:
            size_str = f", {img.size} bytes" if img.size else ""
            file_lines.append(f"- Image: {img.local_path} ({img.mimetype}{size_str})")

        for f in other_files:
            size_str = f", {f.size} bytes" if f.size else ""
            file_lines.append(f"- File: {f.local_path} ({f.mimetype}{size_str})")

        file_info = "\n".join(file_lines)

        # If there's no text message, just use file info (without leading newline)
        if not request.message or not request.message.strip():
            return file_info.lstrip()

        # Append file info to message
        return f"{request.message}{file_info}"
