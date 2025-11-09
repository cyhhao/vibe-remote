import asyncio
import logging
import os
from typing import Callable, Optional

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

    async def handle_message(self, request: AgentRequest) -> None:
        context = request.context

        try:
            client = await self.session_handler.get_or_create_claude_session(context)

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
            request.context, "system", "🛑 Interrupting Claude session..."
        )
        try:
            if hasattr(client, "interrupt"):
                await client.interrupt()
            else:
                # Fallback: disconnect and drop session
                await client.disconnect()
                await self.session_handler.cleanup_session(composite_key)
            return True
        except Exception as err:
            logger.error(f"Failed to interrupt Claude session {composite_key}: {err}")
            await self.controller.emit_agent_message(
                request.context,
                "system",
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
            async for message in client.receive_messages():
                try:
                    claude_session_id = self._maybe_capture_session_id(
                        message, base_session_id, working_path, settings_key
                    )
                    if claude_session_id:
                        logger.info(
                            f"Captured Claude session id {claude_session_id} for {base_session_id}"
                        )

                    if self.claude_client._is_skip_message(message):
                        continue

                    message_type = self._detect_message_type(message)
                    if message_type and self.settings_manager.is_message_type_hidden(
                        settings_key, message_type
                    ):
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

                    await self.controller.emit_agent_message(
                        context,
                        message_type or "assistant",
                        formatted_message,
                        parse_mode="markdown",
                    )

                    if message_type == "result":
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
