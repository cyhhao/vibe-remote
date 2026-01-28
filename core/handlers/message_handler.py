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
            # Record user activity for auto-update idle detection
            if hasattr(self.controller, "update_checker"):
                self.controller.update_checker.record_activity()

            # If message is empty (e.g., user just @mentioned bot without text),
            # trigger the /start command instead of sending empty message to agent
            if not message or not message.strip():
                await self.controller.command_handler.handle_start(context, "")
                return

            # Deduplication: check if this message has already been processed
            # This prevents duplicate processing when vibe-remote restarts and
            # Slack resends events
            message_ts = context.message_id
            thread_ts = context.thread_id or context.message_id
            if message_ts and thread_ts:
                if self.settings_manager.is_message_already_processed(
                    context.channel_id, thread_ts, message_ts
                ):
                    logger.info(
                        f"Skipping already processed message: channel={context.channel_id}, "
                        f"thread={thread_ts}, message={message_ts}"
                    )
                    return
                # Record this message as processed immediately to prevent duplicates
                # even if processing fails (we don't want to retry failed messages forever)
                self.settings_manager.record_processed_message(
                    context.channel_id, thread_ts, message_ts
                )

            # Skip automatic cleanup; receiver tasks are retained until shutdown

            # Allow "stop" shortcut inside Slack threads
            if context.thread_id and message.strip().lower() in ["stop", "/stop"]:
                if await self._handle_inline_stop(context):
                    return

            if not self.session_handler:
                raise RuntimeError("Session handler not initialized")

            base_session_id, working_path, composite_key = (
                self.session_handler.get_session_info(context)
            )
            settings_key = self._get_settings_key(context)

            agent_name = self.controller.resolve_agent_for_context(context)

            matched_prefix = None
            subagent_message = None
            subagent_name = None
            subagent_model = None
            subagent_reasoning_effort = None

            if agent_name in ["opencode", "claude"]:
                from modules.agents.subagent_router import (
                    load_claude_subagent,
                    normalize_subagent_name,
                    parse_subagent_prefix,
                )

                parsed = parse_subagent_prefix(message)
                if parsed:
                    normalized = normalize_subagent_name(parsed.name)
                    if agent_name == "opencode":
                        try:
                            opencode_agent = self.controller.agent_service.agents.get(
                                "opencode"
                            )
                            if opencode_agent and hasattr(
                                opencode_agent, "_get_server"
                            ):
                                server = await opencode_agent._get_server()
                                await server.ensure_running()
                                opencode_agents = await server.get_available_agents(
                                    self.controller.get_cwd(context)
                                )
                                name_map = {
                                    normalize_subagent_name(a.get("name", "")): a
                                    for a in opencode_agents
                                    if a.get("name")
                                }
                                match = name_map.get(normalized)
                                if match:
                                    subagent_name = match.get("name")
                        except Exception as err:
                            logger.warning(
                                f"Failed to resolve OpenCode subagent: {err}"
                            )
                    else:
                        try:
                            subagent_def = load_claude_subagent(normalized)
                            if subagent_def:
                                subagent_name = subagent_def.name
                                subagent_model = subagent_def.model
                                subagent_reasoning_effort = (
                                    subagent_def.reasoning_effort
                                )
                        except Exception as err:
                            logger.warning(f"Failed to resolve Claude subagent: {err}")

                    if subagent_name:
                        matched_prefix = parsed.name
                        subagent_message = parsed.message

            if subagent_name and subagent_message:
                message = subagent_message
                if agent_name == "claude":
                    base_session_id = f"{base_session_id}:{subagent_name}"
                    composite_key = f"{base_session_id}:{working_path}"

            ack_message_id = None
            ack_mode = getattr(self.config, "ack_mode", "reaction")
            ack_reaction_message_id = None
            ack_reaction_emoji = None

            if ack_mode == "message":
                ack_context = self._get_target_context(context)
                ack_text = self._get_ack_text(agent_name)
                try:
                    ack_message_id = await self.im_client.send_message(
                        ack_context, ack_text
                    )
                except Exception as ack_err:
                    logger.debug(f"Failed to send ack message: {ack_err}")
            else:
                # Default: add 👀 / :eyes: reaction to the user's message
                try:
                    if context.message_id:
                        ack_reaction_message_id = context.message_id
                        ack_reaction_emoji = ":eyes:"
                        ok = await self.im_client.add_reaction(
                            context, ack_reaction_message_id, ack_reaction_emoji
                        )
                        if not ok:
                            logger.info(
                                "Ack reaction not applied (platform returned False)"
                            )
                except Exception as ack_err:
                    logger.debug(f"Failed to add reaction ack: {ack_err}")

            if subagent_name and context.message_id:
                try:
                    reaction = ":robot_face:"
                    await self.im_client.add_reaction(
                        context,
                        context.message_id,
                        reaction,
                    )
                except Exception as err:
                    logger.debug(f"Failed to add subagent reaction: {err}")
                if ack_reaction_message_id and ack_reaction_emoji:
                    try:
                        await self.im_client.remove_reaction(
                            context, ack_reaction_message_id, ack_reaction_emoji
                        )
                    except Exception as err:
                        logger.debug(
                            f"Failed to remove reaction ack for subagent: {err}"
                        )

            request = AgentRequest(
                context=context,
                message=message,
                working_path=working_path,
                base_session_id=base_session_id,
                composite_session_id=composite_key,
                settings_key=settings_key,
                ack_message_id=ack_message_id,
                subagent_name=subagent_name,
                subagent_key=matched_prefix,
                subagent_model=subagent_model,
                subagent_reasoning_effort=subagent_reasoning_effort,
                # Pass reaction info for agents to remove when result is sent
                ack_reaction_message_id=ack_reaction_message_id
                if not subagent_name
                else None,
                ack_reaction_emoji=ack_reaction_emoji if not subagent_name else None,
            )
            try:
                await self.controller.agent_service.handle_message(agent_name, request)
            except KeyError:
                await self._handle_missing_agent(context, agent_name)
                # Clean up reaction on error
                await self._remove_ack_reaction(context, request)
            finally:
                if request.ack_message_id:
                    await self._delete_ack(context.channel_id, request)
        except Exception as e:
            logger.error(f"Error processing user message: {e}", exc_info=True)
            # Clean up reaction on any exception
            # Use try/except to safely access possibly-unbound local variables
            try:
                try:
                    # Try using request object if it was created
                    if request.ack_reaction_message_id:  # type: ignore[possibly-undefined]
                        await self._remove_ack_reaction(context, request)  # type: ignore[possibly-undefined]
                except NameError:
                    # request not defined yet, try using local variables
                    if (
                        ack_reaction_message_id  # type: ignore[possibly-undefined]
                        and ack_reaction_emoji  # type: ignore[possibly-undefined]
                        and not subagent_name  # type: ignore[possibly-undefined]
                    ):
                        await self.im_client.remove_reaction(
                            context, ack_reaction_message_id, ack_reaction_emoji
                        )
            except Exception as cleanup_err:
                logger.debug(f"Failed to clean up reaction on error: {cleanup_err}")
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
            from .command_handlers import CommandHandlers

            settings_handler = SettingsHandler(self.controller)
            command_handlers = CommandHandlers(self.controller)

            # Route based on callback data
            if callback_data.startswith("toggle_msg_"):
                # Toggle message type visibility
                msg_type = callback_data.replace("toggle_msg_", "")
                await settings_handler.handle_toggle_message_type(context, msg_type)
            elif callback_data.startswith("toggle_"):
                # Legacy toggle handler (if any)
                setting_type = callback_data.replace("toggle_", "")
                handler = getattr(settings_handler, "handle_toggle_setting", None)
                if handler:
                    await handler(context, setting_type)

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

            elif callback_data == "cmd_resume":
                await command_handlers.handle_resume(context)

            elif callback_data == "cmd_settings":
                await settings_handler.handle_settings(context)

            elif callback_data == "cmd_routing":
                await settings_handler.handle_routing(context)

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

            elif callback_data.startswith("opencode_question:"):
                if not self.session_handler:
                    raise RuntimeError("Session handler not initialized")

                base_session_id, working_path, composite_key = (
                    self.session_handler.get_session_info(context)
                )
                settings_key = self._get_settings_key(context)
                request = AgentRequest(
                    context=context,
                    message=callback_data,
                    working_path=working_path,
                    base_session_id=base_session_id,
                    composite_session_id=composite_key,
                    settings_key=settings_key,
                )
                await self.controller.agent_service.handle_message("opencode", request)

            else:
                logger.warning(f"Unknown callback data: {callback_data}")
                await self.im_client.send_message(
                    context,
                    self.formatter.format_warning(f"Unknown action: {callback_data}"),
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
            if not self.session_handler:
                raise RuntimeError("Session handler not initialized")

            base_session_id, working_path, composite_key = (
                self.session_handler.get_session_info(context)
            )
            settings_key = self._get_settings_key(context)
            agent_name = self.controller.resolve_agent_for_context(context)
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

    async def _delete_ack(self, channel_id: str, request: AgentRequest):
        """Delete acknowledgement message if it still exists."""
        if request.ack_message_id and hasattr(self.im_client, "delete_message"):
            try:
                await self.im_client.delete_message(channel_id, request.ack_message_id)
            except Exception as err:
                logger.debug(f"Failed to delete ack message: {err}")
            finally:
                request.ack_message_id = None

    async def _remove_ack_reaction(
        self, context: MessageContext, request: AgentRequest
    ):
        """Remove acknowledgement reaction if it still exists."""
        if request.ack_reaction_message_id and request.ack_reaction_emoji:
            try:
                await self.im_client.remove_reaction(
                    context,
                    request.ack_reaction_message_id,
                    request.ack_reaction_emoji,
                )
            except Exception as err:
                logger.debug(f"Failed to remove reaction ack: {err}")
            finally:
                request.ack_reaction_message_id = None
                request.ack_reaction_emoji = None

    def _get_ack_text(self, agent_name: str) -> str:
        """Unified acknowledgement text before agent processing."""
        label = agent_name or self.controller.agent_service.default_agent
        return f"📨 {label.capitalize()} received, processing..."
