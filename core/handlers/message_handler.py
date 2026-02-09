"""Message routing and Agent communication handlers"""

import logging
from typing import Optional, List

from modules.agents import AgentRequest
from modules.im import MessageContext
from modules.im.base import FileAttachment
from vibe.i18n import t as i18n_t

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

    def _get_lang(self) -> str:
        if hasattr(self.controller, "_get_lang"):
            return self.controller._get_lang()
        return getattr(self.config, "language", "en")

    def _t(self, key: str, **kwargs) -> str:
        return i18n_t(key, self._get_lang(), **kwargs)

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

            # If message is empty AND no files attached (e.g., user just @mentioned bot without text),
            # trigger the /start command instead of sending empty message to agent
            has_files = bool(context.files)
            if (not message or not message.strip()) and not has_files:
                await self.controller.command_handler.handle_start(context, "")
                return

            # Deduplication: check if this message has already been processed
            # This prevents duplicate processing when vibe-remote restarts and
            # Slack resends events
            message_ts = context.message_id
            thread_ts = context.thread_id or context.message_id
            if message_ts and thread_ts:
                if self.settings_manager.is_message_already_processed(context.channel_id, thread_ts, message_ts):
                    logger.info(
                        f"Skipping already processed message: channel={context.channel_id}, "
                        f"thread={thread_ts}, message={message_ts}"
                    )
                    return
                # Record this message as processed immediately to prevent duplicates
                # even if processing fails (we don't want to retry failed messages forever)
                self.settings_manager.record_processed_message(context.channel_id, thread_ts, message_ts)

            # Skip automatic cleanup; receiver tasks are retained until shutdown

            # Allow "stop" shortcut inside Slack threads
            if context.thread_id and message.strip().lower() in ["stop", "/stop"]:
                if await self._handle_inline_stop(context):
                    return

            if not self.session_handler:
                raise RuntimeError("Session handler not initialized")

            base_session_id, working_path, composite_key = self.session_handler.get_session_info(context)
            settings_key = self._get_settings_key(context)

            # Update thread's current message_id so log messages follow this user message
            # This is critical for proper log message grouping when agent receivers
            # hold references to older contexts
            self.controller.update_thread_message_id(context)

            agent_name = self.controller.resolve_agent_for_context(context)

            # Check for routing-based agent to maintain session key consistency
            # This ensures session IDs match between MessageHandler and SessionHandler
            routing = self.controller.settings_manager.get_channel_routing(settings_key)
            routing_agent = routing.claude_agent if routing else None

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
                            opencode_agent = self.controller.agent_service.agents.get("opencode")
                            if opencode_agent and hasattr(opencode_agent, "_get_server"):
                                server = await opencode_agent._get_server()
                                await server.ensure_running()
                                opencode_agents = await server.get_available_agents(self.controller.get_cwd(context))
                                name_map = {
                                    normalize_subagent_name(a.get("name", "")): a
                                    for a in opencode_agents
                                    if a.get("name")
                                }
                                match = name_map.get(normalized)
                                if match:
                                    subagent_name = match.get("name")
                        except Exception as err:
                            logger.warning(f"Failed to resolve OpenCode subagent: {err}")
                    else:
                        try:
                            from pathlib import Path

                            subagent_def = load_claude_subagent(
                                normalized,
                                project_root=Path(working_path),
                            )
                            if subagent_def:
                                subagent_name = subagent_def.name
                                subagent_model = subagent_def.model
                                subagent_reasoning_effort = subagent_def.reasoning_effort
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
            elif agent_name == "claude" and routing_agent and not subagent_name:
                # Update session IDs for routing-based agent to match SessionHandler
                base_session_id = f"{base_session_id}:{routing_agent}"
                composite_key = f"{base_session_id}:{working_path}"

            ack_message_id = None
            ack_mode = getattr(self.config, "ack_mode", "reaction")
            ack_reaction_message_id = None
            ack_reaction_emoji = None

            if ack_mode == "message":
                ack_context = self._get_target_context(context)
                ack_text = self._get_ack_text(agent_name)
                try:
                    ack_message_id = await self.im_client.send_message(ack_context, ack_text)
                except Exception as ack_err:
                    logger.debug(f"Failed to send ack message: {ack_err}")
            else:
                # Default: add 👀 / :eyes: reaction to the user's message
                try:
                    if context.message_id:
                        ack_reaction_message_id = context.message_id
                        ack_reaction_emoji = ":eyes:"
                        ok = await self.im_client.add_reaction(context, ack_reaction_message_id, ack_reaction_emoji)
                        if not ok:
                            logger.info("Ack reaction not applied (platform returned False)")
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
                # Keep :eyes: alive — the agent will remove it on result/error
                # via the normal ack_reaction lifecycle.  Previously :eyes: was
                # removed here immediately, leaving no processing indicator
                # for the entire duration of the subagent run.

            # Process file attachments if present
            processed_files = None
            if context.files:
                processed_files = await self._process_file_attachments(context, working_path)
                if processed_files:
                    logger.info(f"Processed {len(processed_files)} file attachments for message")

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
                # Reaction info — agent removes :eyes: on result/error
                ack_reaction_message_id=ack_reaction_message_id,
                ack_reaction_emoji=ack_reaction_emoji,
                files=processed_files,
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
                    ):
                        await self.im_client.remove_reaction(context, ack_reaction_message_id, ack_reaction_emoji)
            except Exception as cleanup_err:
                logger.debug(f"Failed to clean up reaction on error: {cleanup_err}")
            await self.im_client.send_message(
                context,
                self.formatter.format_error(self._t("error.processMessageFailed", error=str(e))),
            )

    async def handle_callback_query(self, context: MessageContext, callback_data: str):
        """Route callback queries to appropriate handlers"""
        try:
            logger.info(f"handle_callback_query called with data: {callback_data} for user {context.user_id}")

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
                logger.info(f"Handling info_msg_types callback for user {context.user_id}")
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

            elif callback_data.startswith("vibe_update_now"):
                # Discord update button handler
                target_version = None
                if ":" in callback_data:
                    target_version = callback_data.split(":", 1)[1] or None
                if hasattr(self.controller, "update_checker"):
                    await self.controller.update_checker.handle_update_button_click(context, target_version)
                else:
                    await self.im_client.send_message(
                        context,
                        self.formatter.format_warning(self._t("error.updateUnavailable")),
                    )

            elif callback_data.startswith("info_") and callback_data != "info_msg_types":
                # Generic info handler
                info_type = callback_data.replace("info_", "")
                info_text = self.formatter.format_info_message(
                    title=self._t("info.genericTitle", topic=info_type),
                    emoji="ℹ️",
                    footer=self._t("info.genericFooter"),
                )
                await self.im_client.send_message(context, info_text)

            elif callback_data.startswith("opencode_question:"):
                if not self.session_handler:
                    raise RuntimeError("Session handler not initialized")

                base_session_id, working_path, composite_key = self.session_handler.get_session_info(context)
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

            elif callback_data.startswith("claude_question:"):
                if not self.session_handler:
                    raise RuntimeError("Session handler not initialized")

                base_session_id, working_path, composite_key = self.session_handler.get_session_info(context)
                settings_key = self._get_settings_key(context)
                request = AgentRequest(
                    context=context,
                    message=callback_data,
                    working_path=working_path,
                    base_session_id=base_session_id,
                    composite_session_id=composite_key,
                    settings_key=settings_key,
                )
                await self.controller.agent_service.handle_message("claude", request)

            else:
                logger.warning(f"Unknown callback data: {callback_data}")
                await self.im_client.send_message(
                    context,
                    self.formatter.format_warning(self._t("error.unknownAction", action=callback_data)),
                )

        except Exception as e:
            logger.error(f"Error handling callback query: {e}", exc_info=True)
            await self.im_client.send_message(
                context,
                self.formatter.format_error(self._t("error.processActionFailed", error=str(e))),
            )

    async def _handle_inline_stop(self, context: MessageContext) -> bool:
        """Route inline 'stop' messages to the active agent."""
        try:
            if not self.session_handler:
                raise RuntimeError("Session handler not initialized")

            base_session_id, working_path, composite_key = self.session_handler.get_session_info(context)
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
                handled = await self.controller.agent_service.handle_stop(agent_name, request)
            except KeyError:
                await self._handle_missing_agent(context, agent_name)
                return False
            if not handled:
                await self.im_client.send_message(context, f"ℹ️ {self._t('command.stop.noActiveSession')}")
            return handled
        except Exception as e:
            logger.error(f"Error handling inline stop: {e}", exc_info=True)
            return False

    async def _handle_missing_agent(self, context: MessageContext, agent_name: str):
        """Notify user when a requested agent backend is unavailable."""
        target = agent_name or self.controller.agent_service.default_agent
        msg = f"❌ {self._t('error.agentNotConfigured', agent=target)}"
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

    async def _remove_ack_reaction(self, context: MessageContext, request: AgentRequest):
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
        agent_label = label.capitalize() if label else ""
        return f"📨 {self._t('message.ack', agent=agent_label)}"

    async def _process_file_attachments(self, context: MessageContext, working_path: str) -> Optional[list]:
        """Download and process file attachments from the message.

        All files (including images) are saved to ~/.vibe_remote/attachments/{channel_id}/
        to avoid polluting the working directory (which is often a git repo).
        The agent can then use Read tools to access them.

        Args:
            context: Message context with file attachments
            working_path: Working directory path (not used for storage, kept for API compat)

        Returns:
            List of processed FileAttachment objects with local_path set
        """
        import os
        import time
        from config.paths import get_attachments_dir
        from modules.im.base import FileAttachment

        if not context.files:
            return None

        # Create channel-specific attachments directory
        # Path: ~/.vibe_remote/attachments/{channel_id}/
        attachments_dir = get_attachments_dir() / context.channel_id
        attachments_dir.mkdir(parents=True, exist_ok=True)

        processed = []
        for attachment in context.files:
            if not isinstance(attachment, FileAttachment):
                continue

            try:
                # Download the file content
                if hasattr(self.im_client, "download_file") and attachment.url:
                    # Create a dict for Slack's download_file method
                    file_info = {
                        "url_private_download": attachment.url,
                        "name": attachment.name,
                        "size": attachment.size,
                    }
                    content = await self.im_client.download_file(file_info)
                    if content:
                        # Generate filename: {timestamp}_{original_name}
                        timestamp = int(time.time())
                        safe_name = self._sanitize_filename(attachment.name)
                        filename = f"{timestamp}_{safe_name}"
                        local_path = attachments_dir / filename

                        with open(local_path, "wb") as f:
                            f.write(content)

                        attachment.local_path = str(local_path)
                        attachment.size = len(content)

                        # Determine file type for logging
                        is_image = (attachment.mimetype or "").startswith("image/")
                        file_type = "image" if is_image else "file"

                        logger.info(f"Saved {file_type} '{attachment.name}' ({len(content)} bytes) to '{local_path}'")

                        processed.append(attachment)
                    else:
                        logger.warning(f"Failed to download file: {attachment.name}")
                else:
                    logger.warning(f"Cannot download file: {attachment.name} (no URL or download method)")

            except Exception as e:
                logger.error(f"Error processing file attachment {attachment.name}: {e}")
                continue

        return processed if processed else None

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to be safe for filesystem.

        Args:
            filename: Original filename

        Returns:
            Sanitized filename safe for filesystem
        """
        import re

        # Remove or replace dangerous characters
        # Keep alphanumeric, dots, hyphens, underscores
        safe = re.sub(r"[^\w\-.]", "_", filename)
        # Prevent directory traversal
        safe = safe.replace("..", "_")
        # Limit length
        if len(safe) > 200:
            base, ext = safe.rsplit(".", 1) if "." in safe else (safe, "")
            safe = base[:195] + ("." + ext if ext else "")
        return safe or "unnamed_file"
