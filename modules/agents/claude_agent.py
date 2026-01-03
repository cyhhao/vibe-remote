import asyncio
import logging
import os
from typing import Callable, Optional

from claude_code_sdk import TextBlock

from modules.agents.base import AgentRequest, BaseAgent
from modules.im import MessageContext

logger = logging.getLogger(__name__)


class ClaudeAgent(BaseAgent):
    """Existing Claude Code integration extracted into an agent backend."""

    name = "claude"

    def __init__(self, controller):
        super().__init__(controller)
        self.session_handler = controller.session_handler
        self.session_manager = controller.session_manager
        self.receiver_tasks = controller.receiver_tasks
        self.claude_sessions = controller.claude_sessions
        self.claude_client = controller.claude_client
        self._last_assistant_text: dict[str, str] = {}
        # Track active status updaters per session
        self._status_updaters: dict[str, "StatusUpdater"] = {}

    async def handle_message(self, request: AgentRequest) -> None:
        context = request.context

        try:
            client = await self.session_handler.get_or_create_claude_session(context)

            # Track status updater for this session
            if request.status_updater:
                self._status_updaters[request.composite_session_id] = request.status_updater

            # Mark session as active when starting to process a message
            self.controller.mark_session_active(request.composite_session_id)

            await client.query(
                request.message, session_id=request.composite_session_id
            )
            logger.info(
                f"Sent message to Claude for session {request.composite_session_id}"
            )

            await self._delete_ack(context, request)

            if (
                request.composite_session_id not in self.receiver_tasks
                or self.receiver_tasks[request.composite_session_id].done()
            ):
                self.receiver_tasks[request.composite_session_id] = asyncio.create_task(
                    self._receive_messages(
                        client, request.base_session_id, request.working_path, context
                    )
                )
        except Exception as e:
            logger.error(f"Error processing Claude message: {e}", exc_info=True)
            await self.session_handler.handle_session_error(
                request.composite_session_id, context, e
            )
        finally:
            await self._delete_ack(context, request)

    async def clear_sessions(self, settings_key: str) -> int:
        """Clear Claude sessions scoped to the provided settings key."""
        settings = self.settings_manager.get_user_settings(settings_key)
        claude_map = settings.session_mappings.get(self.name, {})
        session_bases_to_clear = set(claude_map.keys())

        self.settings_manager.clear_agent_sessions(settings_key, self.name)

        sessions_to_clear = []
        for session_key in list(self.claude_sessions.keys()):
            base_part = session_key.split(":")[0] if ":" in session_key else session_key
            if base_part in session_bases_to_clear:
                sessions_to_clear.append(session_key)

        for session_key in sessions_to_clear:
            try:
                client = self.claude_sessions[session_key]
                if hasattr(client, "close"):
                    await client.close()
            except Exception as e:
                logger.warning(f"Error closing Claude session {session_key}: {e}")
            finally:
                self.claude_sessions.pop(session_key, None)

        # Legacy session manager cleanup (best-effort)
        await self.session_manager.clear_session(settings_key)

        return len(sessions_to_clear) or len(session_bases_to_clear)

    async def handle_stop(self, request: AgentRequest) -> bool:
        composite_key = request.composite_session_id
        if composite_key not in self.claude_sessions:
            return False

        client = self.claude_sessions[composite_key]
        await self.controller.emit_agent_message(
            request.context, "notify", "🛑 Interrupting Claude session..."
        )
        try:
            if hasattr(client, "interrupt"):
                await client.interrupt()
                return True
            else:
                await self.controller.emit_agent_message(
                    request.context,
                    "notify",
                    "⚠️ This Claude session cannot be interrupted; consider /clear.",
                )
                return False
        except Exception as err:
            logger.error(f"Failed to interrupt Claude session {composite_key}: {err}")
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "⚠️ Failed to interrupt Claude session. Please try /clear.",
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
            settings_key = self.controller._get_settings_key(context)
            composite_key = f"{base_session_id}:{working_path}"
            async for message in client.receive_messages():
                try:
                    # Update activity timestamp on each message received
                    self.controller.mark_session_active(composite_key)

                    claude_session_id = self._maybe_capture_session_id(
                        message, base_session_id, working_path, settings_key
                    )
                    if claude_session_id:
                        logger.info(
                            f"Captured Claude session id {claude_session_id} for {base_session_id}"
                        )

                    if self.claude_client._is_skip_message(message):
                        continue

                    # Update status based on message content
                    self._update_status_from_message(composite_key, message)

                    # Extract and send images first
                    images = self.claude_client.extract_images_from_message(message)
                    for image in images:
                        try:
                            await self.im_client.send_photo(
                                self._get_target_context(context),
                                image_data=image.data,
                                caption="📸 Screenshot",
                                filename=image.filename,
                            )
                            logger.info(f"Sent image ({image.media_type}) to user")
                        except Exception as img_err:
                            logger.error(f"Failed to send image: {img_err}")

                    message_type = self._detect_message_type(message)
                    formatted_message = None
                    if message_type == "assistant":
                        formatted_message = self.claude_client.format_message(
                            message,
                            get_relative_path=lambda path: self.get_relative_path(
                                path, context
                            ),
                        )
                        assistant_text = self._extract_text_blocks(message)
                        if assistant_text:
                            self._last_assistant_text[composite_key] = assistant_text
                        if self.settings_manager.is_message_type_hidden(
                            settings_key, message_type
                        ):
                            continue
                    elif message_type == "result":
                        if self.settings_manager.is_message_type_hidden(
                            settings_key, message_type
                        ):
                            self._last_assistant_text.pop(composite_key, None)
                            # Mark session as idle even when result is hidden
                            self.controller.mark_session_idle(composite_key)
                            continue
                        result_text = getattr(message, "result", None)
                        if (
                            not result_text
                            and self.settings_manager.is_message_type_hidden(
                                settings_key, "assistant"
                            )
                        ):
                            fallback = self._last_assistant_text.get(composite_key)
                            if fallback:
                                result_text = fallback
                        suffix = "---" if self.config.platform == "slack" else None
                        # Stop and clean up status updater before result message
                        status_updater = self._status_updaters.pop(composite_key, None)
                        if status_updater:
                            await status_updater.stop()
                        await self.emit_result_message(
                            context,
                            result_text,
                            subtype=getattr(message, "subtype", "") or "",
                            duration_ms=getattr(message, "duration_ms", 0),
                            parse_mode="markdown",
                            suffix=suffix,
                            working_path=working_path,
                            composite_session_id=composite_key,
                        )
                        self._last_assistant_text.pop(composite_key, None)
                        # Mark session as idle after result is processed
                        self.controller.mark_session_idle(composite_key)
                        session = await self.session_manager.get_or_create_session(
                            context.user_id, context.channel_id
                        )
                        if session:
                            session.session_active[
                                f"{base_session_id}:{working_path}"
                            ] = False
                        continue
                    else:
                        if message_type and self.settings_manager.is_message_type_hidden(
                            settings_key, message_type
                        ):
                            if message_type == "result":
                                self._last_assistant_text.pop(composite_key, None)
                            continue
                        formatted_message = self.claude_client.format_message(
                            message,
                            get_relative_path=lambda path: self.get_relative_path(
                                path, context
                            ),
                        )
                    if not formatted_message or not formatted_message.strip():
                        continue

                    if self.config.platform == "slack":
                        formatted_message = formatted_message + "\n---"

                    # Stop status updater before result message
                    if message_type == "result":
                        status_updater = self._status_updaters.pop(composite_key, None)
                        if status_updater:
                            await status_updater.stop()

                    await self.controller.emit_agent_message(
                        context,
                        message_type or "assistant",
                        formatted_message,
                        parse_mode="markdown",
                    )

                    if message_type == "result":
                        self._last_assistant_text.pop(composite_key, None)
                        # Mark session as idle after result is processed
                        self.controller.mark_session_idle(composite_key)
                        session = await self.session_manager.get_or_create_session(
                            context.user_id, context.channel_id
                        )
                        if session:
                            session.session_active[
                                f"{base_session_id}:{working_path}"
                            ] = False
                except Exception as e:
                    logger.error(
                        f"Error processing message from Claude: {e}", exc_info=True
                    )
                    continue
        except Exception as e:
            composite_key = f"{base_session_id}:{working_path}"
            logger.error(
                f"Error in Claude receiver for session {composite_key}: {e}",
                exc_info=True,
            )
            # Clean up status updater on error (don't show completion message)
            status_updater = self._status_updaters.pop(composite_key, None)
            if status_updater:
                await status_updater.stop(update_final=False)
            await self.session_handler.handle_session_error(composite_key, context, e)

    async def _delete_ack(self, context: MessageContext, request: AgentRequest):
        ack_id = request.ack_message_id
        if ack_id and hasattr(self.im_client, "delete_message"):
            try:
                await self.im_client.delete_message(context.channel_id, ack_id)
            except Exception as err:
                logger.debug(f"Could not delete ack message: {err}")
            finally:
                request.ack_message_id = None

    def get_relative_path(
        self, abs_path: str, context: Optional[MessageContext] = None
    ) -> str:
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
        working_path: str,
        settings_key: str,
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
                self.session_handler.capture_session_id(
                    base_session_id, working_path, session_id, settings_key
                )
                return session_id
        return None

    def _extract_text_blocks(self, message) -> str:
        """Extract text-only content blocks for result fallbacks."""
        parts = []
        for block in getattr(message, "content", []) or []:
            if isinstance(block, TextBlock):
                text = block.text.strip() if block.text else ""
                if text:
                    parts.append(self.claude_client.formatter.escape_special_chars(text))
        return "\n\n".join(parts).strip()

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

    def _update_status_from_message(self, composite_key: str, message) -> None:
        """Update the status updater based on message content."""
        status_updater = self._status_updaters.get(composite_key)
        if not status_updater:
            return

        activity = self._extract_activity_from_message(message)
        if activity:
            status_updater.set_activity(activity)

    def _extract_activity_from_message(self, message) -> Optional[str]:
        """Extract a short activity description from a Claude message."""
        class_name = getattr(message, "__class__", type(message)).__name__

        # Handle tool use in AssistantMessage
        if class_name == "AssistantMessage":
            content = getattr(message, "content", []) or []
            for block in content:
                block_type = getattr(block, "__class__", type(block)).__name__
                if block_type == "ToolUseBlock":
                    tool_name = getattr(block, "name", None)
                    if tool_name:
                        return self._describe_tool(tool_name, block)
                elif block_type == "TextBlock":
                    text = getattr(block, "text", "") or ""
                    if text:
                        # Get first line or truncate
                        first_line = text.split("\n")[0].strip()
                        if len(first_line) > 140:
                            first_line = first_line[:137] + "..."
                        return f"Thinking: {first_line}"
            return "Analyzing request"

        # System messages
        if class_name == "SystemMessage":
            subtype = getattr(message, "subtype", None)
            if subtype == "init":
                return "Initializing session"
            return "Processing system event"

        return None

    def _describe_tool(self, tool_name: str, block) -> str:
        """Generate a human-readable description of a tool invocation."""
        tool_input = getattr(block, "input", {}) or {}

        descriptions = {
            "Read": lambda: f"Reading {self._shorten_path(tool_input.get('file_path', 'file'))}",
            "Write": lambda: f"Writing {self._shorten_path(tool_input.get('file_path', 'file'))}",
            "Edit": lambda: f"Editing {self._shorten_path(tool_input.get('file_path', 'file'))}",
            "Bash": lambda: f"Running: {self._shorten_command(tool_input.get('command', 'command'))}",
            "Glob": lambda: f"Searching for {tool_input.get('pattern', 'files')}",
            "Grep": lambda: f"Searching for: {self._shorten_text(tool_input.get('pattern', 'pattern'))}",
            "Task": lambda: f"Running sub-agent: {tool_input.get('description', 'task')}",
            "WebFetch": lambda: "Fetching web content",
            "WebSearch": lambda: f"Searching: {self._shorten_text(tool_input.get('query', 'query'))}",
            "TodoWrite": lambda: "Updating task list",
        }

        if tool_name in descriptions:
            try:
                return descriptions[tool_name]()
            except Exception:
                pass

        return f"Using {tool_name}"

    def _shorten_path(self, path: str, max_len: int = 40) -> str:
        """Shorten a file path for display."""
        if not path:
            return "file"
        if len(path) <= max_len:
            return path
        # Show just the filename or last component
        parts = path.replace("\\", "/").split("/")
        return parts[-1] if parts else path[:max_len]

    def _shorten_command(self, cmd: str, max_len: int = 40) -> str:
        """Shorten a command for display."""
        if not cmd:
            return "command"
        # Get first line
        first_line = cmd.split("\n")[0].strip()
        if len(first_line) <= max_len:
            return first_line
        return first_line[:max_len - 3] + "..."

    def _shorten_text(self, text: str, max_len: int = 30) -> str:
        """Shorten text for display."""
        if not text:
            return ""
        if len(text) <= max_len:
            return text
        return text[:max_len - 3] + "..."
