"""Message routing and Agent communication handlers"""

import logging
from typing import Optional

from modules.agents import AgentRequest
from modules.im import MessageContext

logger = logging.getLogger(__name__)


class MessageHandler:
    """Handles message routing and Claude communication"""

    def __init__(self, controller):
        """Initialize with reference to main controller"""
        self.controller = controller
        self.config = controller.config
        self.im_client = controller.im_client
        self.session_manager = controller.session_manager
        self.settings_manager = controller.settings_manager
        self.formatter = controller.im_client.formatter
        self.session_handler = None  # Will be set after creation
        self.receiver_tasks = controller.receiver_tasks

    def set_session_handler(self, session_handler):
        """Set reference to session handler"""
        self.session_handler = session_handler

    def _get_settings_key(self, context: MessageContext) -> str:
        """Get settings key - delegate to controller"""
        return self.controller._get_settings_key(context)

    def _get_target_context(self, context: MessageContext) -> MessageContext:
        """Get target context for sending messages"""
        # For Slack, use thread for replies if enabled
        if self.im_client.should_use_thread_for_reply() and context.thread_id:
            return MessageContext(
                user_id=context.user_id,
                channel_id=context.channel_id,
                thread_id=context.thread_id,
                message_id=context.message_id,
                platform_specific=context.platform_specific,
            )
        return context

    async def handle_user_message(self, context: MessageContext, message: str):
        """Process regular user messages and route to configured agent"""
        try:
            # Safe cleanup: only remove completed receiver tasks when enabled
            if getattr(self.config, "cleanup_enabled", False):
                try:
                    completed_keys = [
                        key
                        for key, task in list(self.receiver_tasks.items())
                        if task.done()
                    ]
                    for key in completed_keys:
                        del self.receiver_tasks[key]
                        logger.info(
                            f"Safely cleaned completed receiver task for session {key}"
                        )
                except Exception as cleanup_err:
                    logger.debug(f"Safe cleanup skipped due to error: {cleanup_err}")

            # Allow "stop" shortcut inside Slack threads
            if context.thread_id and message.strip().lower() in ["stop", "/stop"]:
                if await self._handle_inline_stop(context):
                    return

            # Check for branch selection input
            if await self._handle_branch_selection_input(context, message):
                return

            base_session_id, working_path, composite_key = (
                self.session_handler.get_session_info(context)
            )
            settings_key = self._get_settings_key(context)

            agent_name = self.controller.agent_router.resolve(
                self.config.platform, settings_key
            )
            ack_context = self._get_target_context(context)
            ack_text = self._get_ack_text(agent_name)
            ack_message_id = None
            try:
                ack_message_id = await self.im_client.send_message(
                    ack_context, ack_text
                )
            except Exception as ack_err:
                logger.debug(f"Failed to send ack message: {ack_err}")

            request = AgentRequest(
                context=context,
                message=message,
                working_path=working_path,
                base_session_id=base_session_id,
                composite_session_id=composite_key,
                settings_key=settings_key,
                ack_message_id=ack_message_id,
            )
            try:
                await self.controller.agent_service.handle_message(agent_name, request)
            except KeyError:
                await self._handle_missing_agent(context, agent_name)
            finally:
                if request.ack_message_id:
                    await self._delete_ack(context.channel_id, request)
        except Exception as e:
            logger.error(f"Error processing user message: {e}", exc_info=True)
            await self.im_client.send_message(
                context, self.formatter.format_error(f"Error: {str(e)}")
            )

    async def handle_callback_query(self, context: MessageContext, callback_data: str):
        """Route callback queries to appropriate handlers"""
        try:
            logger.info(
                f"handle_callback_query called with data: {callback_data} for user {context.user_id}"
            )

            # Import handlers to avoid circular dependency
            from .settings_handler import SettingsHandler

            # Reuse existing handlers to preserve state (e.g., pending newtask selections)
            settings_handler = getattr(self.controller, "settings_handler", None) or SettingsHandler(self.controller)
            command_handlers = getattr(self.controller, "command_handler", None)
            if command_handlers is None:
                from .command_handlers import CommandHandlers
                command_handlers = CommandHandlers(self.controller)

            # Route based on callback data
            if callback_data.startswith("toggle_msg_"):
                # Toggle message type visibility
                msg_type = callback_data.replace("toggle_msg_", "")
                await settings_handler.handle_toggle_message_type(context, msg_type)
            elif callback_data.startswith("toggle_"):
                # Legacy toggle handler (if any)
                setting_type = callback_data.replace("toggle_", "")
                if hasattr(settings_handler, "handle_toggle_setting"):
                    await settings_handler.handle_toggle_setting(context, setting_type)

            elif callback_data == "info_msg_types":
                logger.info(
                    f"Handling info_msg_types callback for user {context.user_id}"
                )
                await settings_handler.handle_info_message_types(context)

            elif callback_data == "info_how_it_works":
                await settings_handler.handle_info_how_it_works(context)

            elif callback_data == "cmd_cwd":
                await command_handlers.handle_cwd(context)

            elif callback_data == "cmd_change_cwd":
                await command_handlers.handle_change_cwd_modal(context)

            elif callback_data == "cmd_clear":
                await command_handlers.handle_clear(context)

            elif callback_data == "cmd_settings":
                await settings_handler.handle_settings(context)

            elif (
                callback_data.startswith("info_") and callback_data != "info_msg_types"
            ):
                # Generic info handler
                info_type = callback_data.replace("info_", "")
                info_text = self.formatter.format_info_message(
                    title=f"Info: {info_type}",
                    emoji="ℹ️",
                    footer="This feature is coming soon!",
                )
                await self.im_client.send_message(context, info_text)
            elif callback_data.startswith("newtask_repo:"):
                token = callback_data.split(":", 1)[1]
                await command_handlers.handle_newtask_callback(context, token)
            elif callback_data.startswith("newtask_branch:"):
                token = callback_data.split(":", 1)[1]
                await command_handlers.handle_newtask_branch_callback(context, token)
            elif callback_data.startswith("newtask_branch_page:"):
                token = callback_data.split(":", 1)[1]
                await command_handlers.handle_newtask_branch_page_callback(context, token)
            elif callback_data.startswith("delete_topic_confirm:"):
                parts = callback_data.split(":", 3)
                if len(parts) == 4:
                    _, chat_id, topic_id, decision = parts
                    confirmed = decision == "yes"
                    await command_handlers.handle_delete_topic_confirmation(
                        context, chat_id, topic_id, confirmed
                    )
                else:
                    await self.im_client.send_message(
                        context, "⚠️ 无效的删除确认参数。", parse_mode="plain"
                    )

            else:
                logger.warning(f"Unknown callback data: {callback_data}")
                await self.im_client.send_message(
                    context,
                    self.formatter.format_warning(f"Unknown action: {callback_data}"),
                    parse_mode="plain",
                )

        except Exception as e:
            logger.error(f"Error handling callback query: {e}", exc_info=True)
            await self.im_client.send_message(
                context,
                self.formatter.format_error(f"Error processing action: {str(e)}"),
            )

    async def _handle_inline_stop(self, context: MessageContext) -> bool:
        """Route inline 'stop' messages to the active agent."""
        try:
            base_session_id, working_path, composite_key = (
                self.session_handler.get_session_info(context)
            )
            settings_key = self._get_settings_key(context)
            agent_name = self.controller.agent_router.resolve(
                self.config.platform, settings_key
            )
            request = AgentRequest(
                context=context,
                message="stop",
                working_path=working_path,
                base_session_id=base_session_id,
                composite_session_id=composite_key,
                settings_key=settings_key,
            )
            try:
                handled = await self.controller.agent_service.handle_stop(
                    agent_name, request
                )
            except KeyError:
                await self._handle_missing_agent(context, agent_name)
                return False
            if not handled:
                await self.im_client.send_message(
                    context, "ℹ️ No active session to stop."
                )
            return handled
        except Exception as e:
            logger.error(f"Error handling inline stop: {e}", exc_info=True)
            return False

    async def _handle_missing_agent(self, context: MessageContext, agent_name: str):
        """Notify user when a requested agent backend is unavailable."""
        target = agent_name or self.controller.agent_service.default_agent
        msg = (
            f"❌ Agent `{target}` is not configured. "
            "Make sure the Codex CLI is installed and environment variables are set "
            "if this channel is routed to Codex."
        )
        await self.im_client.send_message(context, msg)

    async def _handle_branch_selection_input(self, context: MessageContext, message: str) -> bool:
        """Check if user is selecting a branch and handle it"""
        try:
            # Get command handlers
            command_handlers = getattr(self.controller, "command_handler", None)
            if command_handlers is None:
                from .command_handlers import CommandHandlers
                command_handlers = CommandHandlers(self.controller)

            # Check if there's a pending branch selection for this user
            pending_data = None
            for token, data in command_handlers.pending_newtask_branch_requests.items():
                if data.get("chat_id") == context.channel_id and data.get("user_id") == context.user_id and "branches" in data:
                    pending_data = data
                    pending_token = token
                    break

            if not pending_data:
                return False

            branches = pending_data.get("branches", [])
            user_input = message.strip()

            # Check if input is a number
            try:
                index = int(user_input) - 1  # Convert to 0-based index
                if 0 <= index < len(branches):
                    selected_branch = branches[index]
                    # Create a fake callback data
                    await command_handlers.handle_newtask_branch_callback_direct(
                        context, pending_token, selected_branch
                    )
                    return True
            except ValueError:
                pass

            # Check if input matches a branch name (exact or partial)
            matched_branches = [b for b in branches if user_input.lower() in b.lower() or b.lower() == user_input.lower()]
            if len(matched_branches) == 1:
                selected_branch = matched_branches[0]
                await command_handlers.handle_newtask_branch_callback_direct(
                    context, pending_token, selected_branch
                )
                return True
            elif len(matched_branches) > 1:
                # Ambiguous - show matches
                matches_text = "\n".join(f"• {b}" for b in matched_branches)
                await self.im_client.send_message(
                    context,
                    f"⚠️ 匹配到多个分支，请更具体一些：\n{matches_text}",
                    parse_mode="plain"
                )
                return True

            return False

        except Exception as e:
            logger.error(f"Error handling branch selection input: {e}")
            return False

    async def _delete_ack(self, channel_id: str, request: AgentRequest):
        """Delete acknowledgement message if it still exists."""
        if request.ack_message_id and hasattr(self.im_client, "delete_message"):
            try:
                await self.im_client.delete_message(channel_id, request.ack_message_id)
            except Exception as err:
                logger.debug(f"Failed to delete ack message: {err}")
            finally:
                request.ack_message_id = None

    def _get_ack_text(self, agent_name: str) -> str:
        """Unified acknowledgement text before agent processing."""
        label = agent_name or self.controller.agent_service.default_agent
        return f"📨 {label.capitalize()} received, processing..."
