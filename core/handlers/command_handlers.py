"""Command handlers for bot commands like /start, /new, /cwd, etc."""

import os
import logging
from typing import Optional
from modules.agents import AgentRequest, get_agent_display_name
from modules.im import MessageContext, InlineKeyboard, InlineButton

from .base import BaseHandler

logger = logging.getLogger(__name__)


class CommandHandlers(BaseHandler):
    """Handles all bot command operations"""

    def __init__(self, controller):
        """Initialize with reference to main controller"""
        super().__init__(controller)
        self.session_manager = controller.session_manager

    def _get_channel_context(self, context: MessageContext) -> MessageContext:
        """Get context for channel messages (no thread)"""
        # Send command responses directly to channel, not in thread/topic
        platform = context.platform or (context.platform_specific or {}).get("platform") or self.config.platform
        if platform in {"slack", "discord", "lark"}:
            return MessageContext(
                user_id=context.user_id,
                channel_id=context.channel_id,
                platform=context.platform,
                thread_id=None,  # No thread for command responses
                platform_specific=context.platform_specific,
            )
        # For other platforms, keep original context
        return context

    def _build_non_interactive_start_message(
        self,
        *,
        platform_name: str,
        agent_display_name: str,
        user_name: str,
        show_channel: bool,
        channel_name: str,
        supports_threads: bool = False,
    ) -> str:
        lines = [
            f"{self._t('command.start.welcome')}",
            "",
            self._t("command.start.greeting", name=user_name),
            self._t("command.start.platform", platform=platform_name),
            self._t("command.start.agent", agent=agent_display_name),
        ]
        if show_channel:
            lines.append(self._t("command.start.channel", channel=channel_name))

        commands = [
            "",
            self._t("command.start.commandsTitle"),
            self._t("command.start.commandStart"),
            self._t("command.start.commandCwd"),
            self._t("command.start.commandSetCwd"),
            self._t("command.start.commandStop", agent=agent_display_name),
        ]
        if not supports_threads:
            commands.append(self._t("command.start.commandNew"))
        lines.extend(commands)
        return "\n".join(line for line in lines if line)

    async def handle_start(self, context: MessageContext, args: str = ""):
        """Handle /start command with interactive buttons"""
        im_client = self._get_im_client(context)
        platform = context.platform or (context.platform_specific or {}).get("platform") or self.config.platform
        platform_name = str(platform).capitalize()

        # Get user and channel info
        try:
            user_info = await im_client.get_user_info(context.user_id)
        except Exception as e:
            logger.warning(f"Failed to get user info: {e}")
            user_info = {"id": context.user_id}

        try:
            channel_info = await im_client.get_channel_info(context.channel_id)
        except Exception as e:
            logger.warning(f"Failed to get channel info: {e}")
            channel_info = {
                "id": context.channel_id,
                "name": (
                    self._t("command.start.directMessage") if context.channel_id.startswith("D") else context.channel_id
                ),
            }

        agent_name = self.controller.resolve_agent_for_context(context)
        default_agent = getattr(self.controller.agent_service, "default_agent", None)
        agent_display_name = get_agent_display_name(agent_name, fallback=default_agent or "Unknown")

        # Determine whether this conversation supports threads.
        # If it does, each new thread is already a fresh session, so the
        # "New Session" button/command is unnecessary.
        is_dm = bool((context.platform_specific or {}).get("is_dm", False))
        supports_threads = (
            getattr(im_client, "should_use_thread_for_dm_session", lambda: False)()
            if is_dm
            else getattr(im_client, "should_use_thread_for_reply", lambda: False)()
        )

        # For non-interactive platforms, use traditional text message
        if platform not in {"slack", "discord", "lark"}:
            user_name = self._resolve_user_display_name(user_info, self._t("command.start.userFallback"))
            show_channel = platform != "wechat"
            message_text = self._build_non_interactive_start_message(
                platform_name=platform_name,
                agent_display_name=agent_display_name,
                user_name=user_name,
                show_channel=show_channel,
                channel_name=channel_info.get("name", "Unknown"),
                supports_threads=supports_threads,
            )
            channel_context = self._get_channel_context(context)
            await im_client.send_message(channel_context, message_text)
            return

        # For Slack/Discord, create interactive buttons
        user_name = self._resolve_user_display_name(user_info, "User")

        # Create interactive buttons for commands
        session_row = []
        if not supports_threads:
            session_row.append(InlineButton(text=f"🆕 {self._t('button.newSession')}", callback_data="cmd_new"))
        session_row.append(InlineButton(text=f"⚙️ {self._t('button.settings')}", callback_data="cmd_settings"))

        buttons = [
            # Row 1: Directory management
            [
                InlineButton(text=f"📁 {self._t('button.currentDir')}", callback_data="cmd_cwd"),
                InlineButton(text=f"📂 {self._t('button.changeDir')}", callback_data="cmd_change_cwd"),
            ],
            # Row 2: Session and/or Settings
            session_row,
            # Row 3: Resume + Agent/Model switching
            [
                InlineButton(text=f"⏮️ {self._t('button.resumeSession')}", callback_data="cmd_resume"),
                InlineButton(text=f"🤖 {self._t('button.agentSettings')}", callback_data="cmd_routing"),
            ],
            # Row 4: Features
            [InlineButton(text=f"✨ {self._t('button.howItWorks')}", callback_data="info_how_it_works")],
        ]

        keyboard = InlineKeyboard(buttons=buttons)

        welcome_text = f"""🎉 **{self._t("command.start.welcome")}**

👋 {self._t("command.start.greeting", name=user_name)}
🔧 {self._t("command.start.platform", platform=platform_name)}
🤖 {self._t("command.start.agent", agent=agent_display_name)}
📍 {self._t("command.start.channel", channel=channel_info.get("name", "Unknown"))}

**{self._t("command.start.quickActions")}**
{self._t("command.start.quickActionsDesc", agent=agent_display_name)}"""

        # Send command response to channel (not in thread)
        channel_context = self._get_channel_context(context)
        await im_client.send_message_with_buttons(channel_context, welcome_text, keyboard)

    async def handle_new(self, context: MessageContext, args: str = ""):
        """Handle /new command - reset active session state for a fresh start."""
        try:
            im_client = self._get_im_client(context)
            settings_key = self._get_settings_key(context)
            await self.controller.agent_service.clear_sessions(settings_key)
            full_response = f"🆕 {self._t('command.new.started')}"

            channel_context = self._get_channel_context(context)
            await im_client.send_message(channel_context, full_response)
            logger.info("Started fresh session for user %s", context.user_id)

        except Exception as e:
            logger.error(f"Error starting new session: {e}", exc_info=True)
            try:
                channel_context = self._get_channel_context(context)
                await im_client.send_message(channel_context, f"❌ {self._t('error.clearSession', error=str(e))}")
            except Exception as send_error:
                logger.error(f"Failed to send error message: {send_error}", exc_info=True)

    async def handle_clear(self, context: MessageContext, args: str = ""):
        """Backward-compatible alias for older interactive callbacks."""

        await self.handle_new(context, args)

    async def handle_cwd(self, context: MessageContext, args: str = ""):
        """Handle cwd command - show current working directory"""
        try:
            im_client = self._get_im_client(context)
            # Get CWD based on context (channel/chat)
            absolute_path = self.controller.get_cwd(context)

            # Build response using formatter to avoid escaping issues
            formatter = self._get_formatter(context)

            # Format path properly with code block
            path_line = f"📁 {self._t('command.cwd.current')}\n{formatter.format_code_inline(absolute_path)}"

            # Build status lines
            status_lines = []
            if os.path.exists(absolute_path):
                status_lines.append(f"✅ {self._t('command.cwd.exists')}")
            else:
                status_lines.append(f"⚠️ {self._t('command.cwd.notExists')}")

            status_lines.append(f"💡 {self._t('command.cwd.hint')}")

            # Combine all parts
            response_text = path_line + "\n" + "\n".join(status_lines)

            channel_context = self._get_channel_context(context)
            await im_client.send_message(channel_context, response_text)
        except Exception as e:
            logger.error(f"Error getting cwd: {e}")
            channel_context = self._get_channel_context(context)
            await im_client.send_message(channel_context, f"❌ {self._t('error.cwdGetFailed', error=str(e))}")

    async def handle_set_cwd(self, context: MessageContext, args: str):
        """Handle set_cwd command - change working directory"""
        try:
            im_client = self._get_im_client(context)
            settings_manager = self._get_settings_manager(context)
            if not args:
                channel_context = self._get_channel_context(context)
                await im_client.send_message(channel_context, self._t("command.cwd.usage"))
                return

            new_path = args.strip()

            # Expand user path and get absolute path
            expanded_path = os.path.expanduser(new_path)
            absolute_path = os.path.abspath(expanded_path)

            # Check if directory exists
            if not os.path.exists(absolute_path):
                # Try to create it
                try:
                    os.makedirs(absolute_path, exist_ok=True)
                    logger.info(f"Created directory: {absolute_path}")
                except Exception as e:
                    channel_context = self._get_channel_context(context)
                    await im_client.send_message(
                        channel_context, f"❌ {self._t('error.cwdCreateFailed', error=str(e))}"
                    )
                    return

            if not os.path.isdir(absolute_path):
                formatter = self._get_formatter(context)
                error_text = f"❌ {self._t('error.cwdNotDirectory', path=formatter.format_code_inline(absolute_path))}"
                channel_context = self._get_channel_context(context)
                await im_client.send_message(channel_context, error_text)
                return

            # Save to user settings
            settings_key = self._get_settings_key(context)
            settings_manager.set_custom_cwd(settings_key, absolute_path)

            logger.info(f"User {context.user_id} changed cwd to: {absolute_path}")

            formatter = self._get_formatter(context)
            response_text = f"✅ {self._t('success.cwdChanged', path=formatter.format_code_inline(absolute_path))}"
            channel_context = self._get_channel_context(context)
            await im_client.send_message(channel_context, response_text)

        except Exception as e:
            logger.error(f"Error setting cwd: {e}")
            channel_context = self._get_channel_context(context)
            await im_client.send_message(channel_context, f"❌ {self._t('error.cwdSetFailed', error=str(e))}")

    async def handle_change_cwd_submission(
        self,
        user_id: str,
        new_cwd: str,
        channel_id: Optional[str] = None,
        is_dm: bool = False,
        platform: Optional[str] = None,
    ):
        """Handle working directory change submission from modal."""
        try:
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id if channel_id else user_id,
                platform=platform or self.config.platform,
                platform_specific={"is_dm": is_dm},
            )
            await self.handle_set_cwd(context, new_cwd.strip())
        except Exception as e:
            logger.error(f"Error changing working directory: {e}")
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id if channel_id else user_id,
                platform=platform or self.config.platform,
                platform_specific={"is_dm": is_dm},
            )
            await self._get_im_client(context).send_message(
                context,
                f"❌ {self._t('error.cwdSetFailed', error=str(e))}",
            )

    async def handle_change_cwd_modal(self, context: MessageContext):
        """Handle Change Work Dir button - open modal for Slack"""
        platform = context.platform or (context.platform_specific or {}).get("platform") or self.config.platform
        im_client = self._get_im_client(context)
        if platform == "discord":
            interaction = context.platform_specific.get("interaction") if context.platform_specific else None
            if interaction and hasattr(im_client, "open_change_cwd_modal"):
                try:
                    current_cwd = self.controller.get_cwd(context)
                    await im_client.run_on_client_loop(
                        im_client.open_change_cwd_modal(interaction, current_cwd, context.channel_id)
                    )
                    return
                except Exception as e:
                    logger.error(f"Error opening change CWD modal: {e}")
            channel_context = self._get_channel_context(context)
            await im_client.send_message(
                channel_context,
                f"📂 {self._t('command.cwd.changeInstructions')}",
            )
            return
        if platform == "lark":
            if hasattr(im_client, "open_change_cwd_modal"):
                try:
                    current_cwd = self.controller.get_cwd(context)
                    await im_client.run_on_client_loop(
                        im_client.open_change_cwd_modal(
                            trigger_id=context,
                            current_cwd=current_cwd,
                            channel_id=context.channel_id,
                        )
                    )
                    return
                except Exception as e:
                    logger.error(f"Error opening change CWD card for Lark: {e}")
            channel_context = self._get_channel_context(context)
            await im_client.send_message(
                channel_context,
                f"📂 {self._t('command.cwd.changeInstructions')}",
            )
            return

        if platform not in {"slack"}:
            channel_context = self._get_channel_context(context)
            await im_client.send_message(
                channel_context,
                f"📂 {self._t('command.cwd.changeInstructions')}",
            )
            return

        # For Slack, open a modal dialog
        trigger_id = context.platform_specific.get("trigger_id") if context.platform_specific else None

        if trigger_id and hasattr(im_client, "open_change_cwd_modal"):
            try:
                # Get current CWD based on context
                current_cwd = self.controller.get_cwd(context)

                await im_client.run_on_client_loop(
                    im_client.open_change_cwd_modal(trigger_id, current_cwd, context.channel_id)
                )
            except Exception as e:
                logger.error(f"Error opening change CWD modal: {e}")
                channel_context = self._get_channel_context(context)
                await im_client.send_message(
                    channel_context,
                    f"❌ {self._t('error.cwdChangeFailed')}",
                )
        else:
            # No trigger_id, show instructions
            channel_context = self._get_channel_context(context)
            await im_client.send_message(
                channel_context,
                f"📂 {self._t('command.cwd.clickButton')}",
            )

    async def handle_resume(self, context: MessageContext):
        """Open resume-session modal (Slack) or explain availability."""
        platform = context.platform or (context.platform_specific or {}).get("platform") or self.config.platform
        im_client = self._get_im_client(context)
        if platform == "discord":
            interaction = context.platform_specific.get("interaction") if context.platform_specific else None
            settings_key = self._get_settings_key(context)
            sessions_by_agent = self.sessions.list_all_agent_sessions(settings_key)
            if not sessions_by_agent:
                channel_context = self._get_channel_context(context)
                await im_client.send_message(
                    channel_context,
                    f"ℹ️ {self._t('command.resume.noStoredSessions')}",
                )
                return
            if interaction and hasattr(im_client, "open_resume_session_modal"):
                try:
                    await im_client.run_on_client_loop(
                        im_client.open_resume_session_modal(
                            trigger_id=interaction,
                            sessions_by_agent=sessions_by_agent,
                            channel_id=context.channel_id,
                            thread_id=context.thread_id or context.message_id or "",
                            host_message_ts=context.message_id,
                        )
                    )
                    return
                except Exception as e:
                    logger.error(f"Error opening resume modal: {e}")
            channel_context = self._get_channel_context(context)
            await im_client.send_message(
                channel_context,
                f"⏮️ {self._t('command.resume.clickButton')}",
            )
            return
        if platform == "lark":
            settings_key = self._get_settings_key(context)
            sessions_by_agent = self.sessions.list_all_agent_sessions(settings_key)
            # Allow opening modal even with no sessions (user can paste manually)
            if hasattr(im_client, "open_resume_session_modal"):
                try:
                    await im_client.run_on_client_loop(
                        im_client.open_resume_session_modal(
                            trigger_id=context,
                            sessions_by_agent=sessions_by_agent or {},
                            channel_id=context.channel_id,
                            thread_id=context.thread_id or context.message_id or "",
                            host_message_ts=context.message_id,
                        )
                    )
                    return
                except Exception as e:
                    logger.error(f"Error opening resume session card for Lark: {e}")
            channel_context = self._get_channel_context(context)
            await im_client.send_message(
                channel_context,
                f"⏮️ {self._t('command.resume.clickButton')}",
            )
            return

        if platform not in {"slack"}:
            channel_context = self._get_channel_context(context)
            await im_client.send_message(
                channel_context,
                f"⏮️ {self._t('command.resume.slackOnly')}",
            )
            return

        trigger_id = context.platform_specific.get("trigger_id") if context.platform_specific else None
        if not trigger_id:
            channel_context = self._get_channel_context(context)
            await im_client.send_message(
                channel_context,
                f"⏮️ {self._t('command.resume.clickButton')}",
            )
            return

        settings_key = self._get_settings_key(context)
        sessions_by_agent = self.sessions.list_all_agent_sessions(settings_key)

        if not sessions_by_agent:
            channel_context = self._get_channel_context(context)
            await im_client.send_message(
                channel_context,
                f"ℹ️ {self._t('command.resume.noStoredSessions')}",
            )

        try:
            await im_client.run_on_client_loop(
                im_client.open_resume_session_modal(
                    trigger_id=trigger_id,
                    sessions_by_agent=sessions_by_agent,
                    channel_id=context.channel_id,
                    thread_id=context.thread_id or context.message_id or "",
                    host_message_ts=context.message_id,
                )
            )
        except Exception as e:
            logger.error(f"Error opening resume modal: {e}")
            channel_context = self._get_channel_context(context)
            await im_client.send_message(channel_context, f"❌ {self._t('error.resumeFailed')}")

    async def handle_bind(self, context: MessageContext, args: str = ""):
        """Handle bind command - bind a user to this Vibe Remote instance via bind code.

        Only allowed in DM context. In channels, instructs the user to DM the bot.
        """
        try:
            im_client = self._get_im_client(context)
            settings_manager = self._get_settings_manager(context)
            platform = context.platform or (context.platform_specific or {}).get("platform") or self.config.platform

            def _is_bound_user() -> bool:
                try:
                    return settings_manager.is_bound_user(context.user_id, platform=platform)
                except TypeError:
                    return settings_manager.is_bound_user(context.user_id)

            def _bind_user(display_name: str):
                try:
                    return settings_manager.bind_user_with_code(
                        context.user_id,
                        display_name,
                        code,
                        dm_chat_id=context.channel_id,
                        platform=platform,
                    )
                except TypeError:
                    return settings_manager.bind_user_with_code(
                        context.user_id,
                        display_name,
                        code,
                        dm_chat_id=context.channel_id,
                    )

            # Check if this is a DM context (settings_key == user_id means DM)
            settings_key = self._get_settings_key(context)
            if settings_key.split("::", 1)[-1] != context.user_id:
                # Not a DM — instruct user to use DM
                channel_context = self._get_channel_context(context)
                await im_client.send_message(channel_context, self._t("bind.dmOnly"))
                return

            code = args.strip()
            if not code:
                channel_context = self._get_channel_context(context)
                await im_client.send_message(channel_context, self._t("bind.usage"))
                return

            # Check if user is already bound
            if _is_bound_user():
                channel_context = self._get_channel_context(context)
                await im_client.send_message(channel_context, self._t("bind.alreadyBound"))
                return

            # Fetch user info for display name
            try:
                user_info = await im_client.get_user_info(context.user_id)
            except Exception as e:
                logger.warning(f"Failed to get user info during bind: {e}")
                user_info = {"id": context.user_id}

            display_name = self._resolve_user_display_name(user_info, context.user_id)

            # Atomic bind: validate code + create user + consume code in one operation
            success, is_admin = _bind_user(display_name)

            if not success:
                # Could be already bound (race) or invalid code
                if _is_bound_user():
                    channel_context = self._get_channel_context(context)
                    await im_client.send_message(channel_context, self._t("bind.alreadyBound"))
                else:
                    channel_context = self._get_channel_context(context)
                    await im_client.send_message(channel_context, self._t("bind.invalidCode"))
                return

            if is_admin:
                msg = self._t("bind.successAdmin", name=display_name)
            else:
                msg = self._t("bind.success", name=display_name)

            channel_context = self._get_channel_context(context)
            await im_client.send_message(channel_context, msg)
            logger.info(f"User {context.user_id} ({display_name}) bound successfully (admin={is_admin})")

        except Exception as e:
            logger.error(f"Error handling bind command: {e}", exc_info=True)
            try:
                channel_context = self._get_channel_context(context)
                await im_client.send_message(channel_context, self._t("bind.error", error=str(e)))
            except Exception as send_error:
                logger.error(f"Failed to send bind error message: {send_error}", exc_info=True)

    async def handle_stop(self, context: MessageContext, args: str = ""):
        """Handle /stop command - send interrupt message to the active agent"""
        try:
            im_client = self._get_im_client(context)
            session_handler = self.controller.session_handler
            base_session_id, working_path, composite_key = session_handler.get_session_info(context)
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

            handled = await self.controller.agent_service.handle_stop(agent_name, request)
            if not handled:
                channel_context = self._get_channel_context(context)
                await im_client.send_message(channel_context, f"ℹ️ {self._t('command.stop.noActiveSession')}")

        except Exception as e:
            logger.error(f"Error sending stop command: {e}", exc_info=True)
            # For errors, still use original context to maintain thread consistency
            await im_client.send_message(
                context,  # Use original context
                f"❌ {self._t('error.stopFailed', error=str(e))}",
            )
