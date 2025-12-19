"""Command handlers for bot commands like /start, /clear, /cwd, etc."""

import os
import logging
from typing import Optional
from modules.agents import AgentRequest, get_agent_display_name
from modules.im import MessageContext, InlineKeyboard, InlineButton

logger = logging.getLogger(__name__)


class CommandHandlers:
    """Handles all bot command operations"""

    def __init__(self, controller):
        """Initialize with reference to main controller"""
        self.controller = controller
        self.config = controller.config
        self.im_client = controller.im_client
        self.session_manager = controller.session_manager
        self.settings_manager = controller.settings_manager
        # Get reference to session_handler which has topic_manager
        self.session_handler = controller.session_handler
        self.topic_manager = self.session_handler.topic_manager

    def _get_channel_context(self, context: MessageContext) -> MessageContext:
        """Get context for channel messages (no thread)"""
        # For Slack: send command responses directly to channel, not in thread
        if self.config.platform == "slack":
            return MessageContext(
                user_id=context.user_id,
                channel_id=context.channel_id,
                thread_id=None,  # No thread for command responses
                platform_specific=context.platform_specific,
            )
        # For other platforms, keep original context
        return context

    async def handle_start(self, context: MessageContext, args: str = ""):
        """Handle /start command with interactive buttons"""
        platform_name = self.config.platform.capitalize()

        # Get user and channel info
        try:
            user_info = await self.im_client.get_user_info(context.user_id)
        except Exception as e:
            logger.warning(f"Failed to get user info: {e}")
            user_info = {"id": context.user_id}

        try:
            channel_info = await self.im_client.get_channel_info(context.channel_id)
        except Exception as e:
            logger.warning(f"Failed to get channel info: {e}")
            channel_info = {
                "id": context.channel_id,
                "name": (
                    "Direct Message"
                    if context.channel_id.startswith("D")
                    else context.channel_id
                ),
            }

        settings_key = self.controller._get_settings_key(context)
        agent_name = self.controller.agent_router.resolve(
            self.config.platform, settings_key
        )
        default_agent = getattr(self.controller.agent_service, "default_agent", None)
        agent_display_name = get_agent_display_name(
            agent_name, fallback=default_agent or "Unknown"
        )

        # For non-Slack platforms, use traditional text message
        if self.config.platform != "slack":
            formatter = self.im_client.formatter

            # Build welcome message using formatter to handle escaping properly
            lines = [
                formatter.format_bold("Welcome to Vibe Remote!"),
                "",
                f"Platform: {formatter.format_text(platform_name)}",
                f"Agent: {formatter.format_text(agent_display_name)}",
                f"User ID: {formatter.format_code_inline(context.user_id)}",
                f"Channel/Chat ID: {formatter.format_code_inline(context.channel_id)}",
                "",
                formatter.format_bold("Commands:"),
                formatter.format_text("/start - Show this message"),
                formatter.format_text("/clear - Reset session and start fresh"),
                formatter.format_text("/cwd - Show current working directory"),
                formatter.format_text("/set_cwd <path> - Set working directory"),
                formatter.format_text("/settings - Personalization settings"),
                formatter.format_text(
                    f"/stop - Interrupt {agent_display_name} execution"
                ),
            ]

            # Add Topic-specific commands for Telegram
            if self._is_telegram_with_topics():
                lines.extend([
                    "",
                    formatter.format_bold("Topic Commands (Telegram Topics):"),
                    formatter.format_text("/list_topics - List all topics"),
                    formatter.format_text("/project_info - Show current project"),
                    formatter.format_text("/git_status - Show git status"),
                    formatter.format_text(
                        "/create_topic <name> - Create new project (manager only)"
                    ),
                    formatter.format_text(
                        "/clone <url> - Clone repo (manager only)"
                    ),
                ])

            lines.extend([
                "",
                formatter.format_bold("How it works:"),
                formatter.format_text(
                    f"• Send any message and it's immediately sent to {agent_display_name}"
                ),
                formatter.format_text(
                    "• Each chat maintains its own conversation context"
                ),
                formatter.format_text("• Use /clear to reset the conversation"),
            ])

            message_text = formatter.format_message(*lines)
            channel_context = self._get_channel_context(context)
            await self.im_client.send_message(channel_context, message_text)
            return

        # For Slack, create interactive buttons using Block Kit
        user_name = user_info.get("real_name") or user_info.get("name") or "User"

        # Create interactive buttons for commands
        buttons = [
            # Row 1: Directory management
            [
                InlineButton(text="📁 Current Dir", callback_data="cmd_cwd"),
                InlineButton(text="📂 Change Work Dir", callback_data="cmd_change_cwd"),
            ],
            # Row 2: Session and Settings
            [
                InlineButton(text="🔄 Clear All Session", callback_data="cmd_clear"),
                InlineButton(text="⚙️ Settings", callback_data="cmd_settings"),
            ],
            # Row 3: Help
            [InlineButton(text="ℹ️ How it Works", callback_data="info_how_it_works")],
        ]

        keyboard = InlineKeyboard(buttons=buttons)

        welcome_text = f"""🎉 **Welcome to Vibe Remote!**

👋 Hello **{user_name}**!
🔧 Platform: **{platform_name}**
🤖 Agent: **{agent_display_name}**
📍 Channel: **{channel_info.get('name', 'Unknown')}**

**Quick Actions:**
Use the buttons below to manage your {agent_display_name} sessions, or simply type any message to start chatting with {agent_display_name}!"""

        # Send command response to channel (not in thread)
        channel_context = self._get_channel_context(context)
        await self.im_client.send_message_with_buttons(
            channel_context, welcome_text, keyboard
        )

    async def handle_clear(self, context: MessageContext, args: str = ""):
        """Handle clear command - clears all sessions across configured agents"""
        try:
            # Get the correct settings key (channel_id for Slack, not user_id)
            settings_key = self.controller._get_settings_key(context)

            cleared = await self.controller.agent_service.clear_sessions(settings_key)
            if not cleared:
                full_response = (
                    "📋 No active sessions to clear.\n🔄 Session state has been reset."
                )
            else:
                details = "\n".join(
                    f"• {agent} → {count} session(s)" for agent, count in cleared.items()
                )
                full_response = (
                    "✅ Cleared active sessions for:\n" f"{details}\n🔄 All sessions reset."
                )

            channel_context = self._get_channel_context(context)
            await self.im_client.send_message(channel_context, full_response)
            logger.info(f"Sent clear response to user {context.user_id}")

        except Exception as e:
            logger.error(f"Error clearing session: {e}", exc_info=True)
            try:
                channel_context = self._get_channel_context(context)
                await self.im_client.send_message(
                    channel_context, f"❌ Error clearing session: {str(e)}"
                )
            except Exception as send_error:
                logger.error(
                    f"Failed to send error message: {send_error}", exc_info=True
                )

    async def handle_cwd(self, context: MessageContext, args: str = ""):
        """Handle cwd command - show current working directory"""
        try:
            # Get CWD based on context (channel/chat)
            absolute_path = self.controller.get_cwd(context)

            # Build response using formatter to avoid escaping issues
            formatter = self.im_client.formatter

            # Format path properly with code block
            path_line = f"📁 Current Working Directory:\n{formatter.format_code_inline(absolute_path)}"

            # Build status lines
            status_lines = []
            if os.path.exists(absolute_path):
                status_lines.append("✅ Directory exists")
            else:
                status_lines.append("⚠️ Directory does not exist")

            # Add Topic information if in Telegram with Topics
            if self._is_telegram_with_topics() and context.thread_id:
                # Check if this topic has a worktree
                worktree_path = self.topic_manager.get_worktree_for_topic(
                    context.channel_id, context.thread_id
                )
                if worktree_path and worktree_path == absolute_path:
                    status_lines.append(f"💬 Topic: {context.thread_id}")
                    status_lines.append("🗂️ Using Topic worktree")

            status_lines.append("💡 This is where Agent will execute commands")

            # Combine all parts
            response_text = path_line + "\n" + "\n".join(status_lines)

            channel_context = self._get_channel_context(context)
            await self.im_client.send_message(channel_context, response_text)
        except Exception as e:
            logger.error(f"Error getting cwd: {e}")
            channel_context = self._get_channel_context(context)
            await self.im_client.send_message(
                channel_context, f"Error getting working directory: {str(e)}"
            )

    async def handle_set_cwd(self, context: MessageContext, args: str):
        """Handle set_cwd command - change working directory"""
        try:
            if not args:
                channel_context = self._get_channel_context(context)
                await self.im_client.send_message(
                    channel_context, "Usage: /set_cwd <path>"
                )
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
                    await self.im_client.send_message(
                        channel_context, f"❌ Cannot create directory: {str(e)}"
                    )
                    return

            if not os.path.isdir(absolute_path):
                formatter = self.im_client.formatter
                error_text = f"❌ Path exists but is not a directory: {formatter.format_code_inline(absolute_path)}"
                channel_context = self._get_channel_context(context)
                await self.im_client.send_message(channel_context, error_text)
                return

            # Save to user settings
            settings_key = self.controller._get_settings_key(context)
            self.settings_manager.set_custom_cwd(settings_key, absolute_path)

            logger.info(f"User {context.user_id} changed cwd to: {absolute_path}")

            formatter = self.im_client.formatter
            response_text = (
                f"✅ Working directory changed to:\n"
                f"{formatter.format_code_inline(absolute_path)}"
            )
            channel_context = self._get_channel_context(context)
            await self.im_client.send_message(channel_context, response_text)

        except Exception as e:
            logger.error(f"Error setting cwd: {e}")
            channel_context = self._get_channel_context(context)
            await self.im_client.send_message(
                channel_context, f"❌ Error setting working directory: {str(e)}"
            )

    async def handle_change_cwd_modal(self, context: MessageContext):
        """Handle Change Work Dir button - open modal for Slack"""
        if self.config.platform != "slack":
            # For non-Slack platforms, just send instructions
            channel_context = self._get_channel_context(context)
            await self.im_client.send_message(
                channel_context,
                "📂 To change working directory, use:\n`/set_cwd <path>`\n\nExample:\n`/set_cwd ~/projects/myapp`",
            )
            return

        # For Slack, open a modal dialog
        trigger_id = (
            context.platform_specific.get("trigger_id")
            if context.platform_specific
            else None
        )

        if trigger_id and hasattr(self.im_client, "open_change_cwd_modal"):
            try:
                # Get current CWD based on context
                current_cwd = self.controller.get_cwd(context)

                await self.im_client.open_change_cwd_modal(
                    trigger_id, current_cwd, context.channel_id
                )
            except Exception as e:
                logger.error(f"Error opening change CWD modal: {e}")
                channel_context = self._get_channel_context(context)
                await self.im_client.send_message(
                    channel_context,
                    "❌ Failed to open directory change dialog. Please try again.",
                )
        else:
            # No trigger_id, show instructions
            channel_context = self._get_channel_context(context)
            await self.im_client.send_message(
                channel_context,
                "📂 Click the 'Change Work Dir' button in the /start menu to change working directory.",
            )

    async def handle_stop(self, context: MessageContext, args: str = ""):
        """Handle /stop command - send interrupt message to the active agent"""
        try:
            session_handler = self.controller.session_handler
            base_session_id, working_path, composite_key = (
                session_handler.get_session_info(context)
            )
            settings_key = self.controller._get_settings_key(context)
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

            handled = await self.controller.agent_service.handle_stop(
                agent_name, request
            )
            if not handled:
                channel_context = self._get_channel_context(context)
                await self.im_client.send_message(
                    channel_context, "ℹ️ No active session to stop for this channel."
                )

        except Exception as e:
            logger.error(f"Error sending stop command: {e}", exc_info=True)
            # For errors, still use original context to maintain thread consistency
            await self.im_client.send_message(
                context,  # Use original context
                f"❌ Error sending stop command: {str(e)}",
            )

    # ---------------------------------------------
    # Topic Management Commands (Telegram Topics)
    # ---------------------------------------------

    def _is_telegram_with_topics(self) -> bool:
        """Check if current platform is Telegram with Topics support"""
        return self.config.platform == "telegram"

    def _check_manager权限(self, context: MessageContext) -> bool:
        """Check if command is executed in manager topic"""
        if not self._is_telegram_with_topics():
            return False

        if not context.thread_id:
            return False

        # Check if this topic is set as manager topic
        settings_key = self.controller._get_settings_key(context)
        manager_topic = self.settings_manager.get_manager_topic(settings_key, context.channel_id)

        return manager_topic == context.thread_id

    async def handle_create_topic(self, context: MessageContext, args: str):
        """Handle /create_topic command - create new project topic"""
        try:
            if not self._is_telegram_with_topics():
                await self.im_client.send_message(
                    context, "❌ This command is only available on Telegram with Topics support."
                )
                return

            if not args:
                await self.im_client.send_message(
                    context, "Usage: /create_topic <project_name>\nExample: /create_topic my-awesome-project"
                )
                return

            # Check if executed in manager topic
            if not self._check_manager权限(context):
                await self.im_client.send_message(
                    context, "❌ This command can only be used in the manager topic."
                )
                return

            project_name = args.strip()

            # Create empty project with worktree
            main_repo_path, worktree_path = self.topic_manager.create_empty_project(
                chat_id=context.channel_id,
                topic_id=context.thread_id,
                project_name=project_name
            )

            # Save to settings
            settings_key = self.controller._get_settings_key(context)
            self.settings_manager.set_topic_worktree(
                settings_key, context.channel_id, context.thread_id, worktree_path
            )

            response = (
                f"✅ Created new project topic:\n"
                f"📂 Project: {project_name}\n"
                f"🆔 Topic ID: {context.thread_id}\n"
                f"📁 Worktree: {worktree_path}\n\n"
                f"💡 You can now use this topic for development work."
            )

            await self.im_client.send_message(context, response)

        except ValueError as e:
            logger.error(f"Error creating topic: {e}")
            await self.im_client.send_message(context, f"❌ Failed to create topic: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error creating topic: {e}", exc_info=True)
            await self.im_client.send_message(context, f"❌ Unexpected error: {str(e)}")

    async def handle_clone(self, context: MessageContext, args: str):
        """Handle /clone command - clone repository and create topic"""
        try:
            if not self._is_telegram_with_topics():
                await self.im_client.send_message(
                    context, "❌ This command is only available on Telegram with Topics support."
                )
                return

            if not args:
                await self.im_client.send_message(
                    context, "Usage: /clone <git_url>\nExample: /clone https://github.com/user/repo.git"
                )
                return

            # Check if executed in manager topic
            if not self._check_manager权限(context):
                await self.im_client.send_message(
                    context, "❌ This command can only be used in the manager topic."
                )
                return

            git_url = args.strip()

            # Clone project with worktree
            main_repo_path, worktree_path = self.topic_manager.clone_project(
                chat_id=context.channel_id,
                topic_id=context.thread_id,
                git_url=git_url
            )

            # Save to settings
            settings_key = self.controller._get_settings_key(context)
            self.settings_manager.set_topic_worktree(
                settings_key, context.channel_id, context.thread_id, worktree_path
            )

            response = (
                f"✅ Cloned repository and created topic:\n"
                f"🔗 Repository: {git_url}\n"
                f"🆔 Topic ID: {context.thread_id}\n"
                f"📁 Worktree: {worktree_path}\n\n"
                f"💡 You can now use this topic for development work."
            )

            await self.im_client.send_message(context, response)

        except ValueError as e:
            logger.error(f"Error cloning repository: {e}")
            await self.im_client.send_message(context, f"❌ Failed to clone repository: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error cloning repository: {e}", exc_info=True)
            await self.im_client.send_message(context, f"❌ Unexpected error: {str(e)}")

    async def handle_list_topics(self, context: MessageContext, args: str):
        """Handle /list_topics command - list all topics"""
        try:
            if not self._is_telegram_with_topics():
                await self.im_client.send_message(
                    context, "❌ This command is only available on Telegram with Topics support."
                )
                return

            # Get all topics for this chat
            topics = self.topic_manager.list_topics(context.channel_id)

            if not topics:
                await self.im_client.send_message(
                    context, "📭 No topics found. Use /create_topic or /clone to create a new project."
                )
                return

            # Build response
            lines = ["📋 **Topics List:**\n"]

            for topic_id, topic_info in topics.items():
                name = topic_info.get("name", "Unknown")
                lines.append(f"• **Topic {topic_id}**: {name}")

                # Mark manager topic
                settings_key = self.controller._get_settings_key(context)
                manager_topic = self.settings_manager.get_manager_topic(settings_key, context.channel_id)
                if manager_topic == topic_id:
                    lines[-1] += " 🔑 (Manager)"

            response = "\n".join(lines)
            await self.im_client.send_message(context, response)

        except Exception as e:
            logger.error(f"Error listing topics: {e}", exc_info=True)
            await self.im_client.send_message(context, f"❌ Error listing topics: {str(e)}")

    async def handle_show_topic(self, context: MessageContext, args: str):
        """Handle /show_topic command - show topic details"""
        try:
            if not self._is_telegram_with_topics():
                await self.im_client.send_message(
                    context, "❌ This command is only available on Telegram with Topics support."
                )
                return

            if not args:
                await self.im_client.send_message(
                    context, "Usage: /show_topic <topic_id>\nExample: /show_topic 123"
                )
                return

            topic_id = args.strip()

            # Get topic info
            topics = self.topic_manager.list_topics(context.channel_id)

            if topic_id not in topics:
                await self.im_client.send_message(context, f"❌ Topic {topic_id} not found.")
                return

            topic_info = topics[topic_id]
            name = topic_info.get("name", "Unknown")

            # Get worktree path
            worktree_path = self.topic_manager.get_worktree_for_topic(
                context.channel_id, topic_id
            )

            # Build response
            lines = [
                f"📋 **Topic Details**",
                f"🆔 Topic ID: {topic_id}",
                f"📂 Project: {name}",
                f"📁 Worktree: {worktree_path or 'Not found'}",
            ]

            # Check if this is manager topic
            settings_key = self.controller._get_settings_key(context)
            manager_topic = self.settings_manager.get_manager_topic(settings_key, context.channel_id)
            if manager_topic == topic_id:
                lines.append("🔑 Type: Manager Topic")

            response = "\n".join(lines)
            await self.im_client.send_message(context, response)

        except Exception as e:
            logger.error(f"Error showing topic: {e}", exc_info=True)
            await self.im_client.send_message(context, f"❌ Error showing topic: {str(e)}")

    async def handle_set_manager_topic(self, context: MessageContext, args: str):
        """Handle /set_manager_topic command - set manager topic"""
        try:
            if not self._is_telegram_with_topics():
                await self.im_client.send_message(
                    context, "❌ This command is only available on Telegram with Topics support."
                )
                return

            if not args:
                await self.im_client.send_message(
                    context, "Usage: /set_manager_topic <topic_id>\nExample: /set_manager_topic 123"
                )
                return

            topic_id = args.strip()

            # Check if topic exists
            topics = self.topic_manager.list_topics(context.channel_id)
            if topic_id not in topics:
                await self.im_client.send_message(context, f"❌ Topic {topic_id} not found.")
                return

            # Set manager topic
            settings_key = self.controller._get_settings_key(context)
            self.settings_manager.set_manager_topic(
                settings_key, context.channel_id, topic_id
            )

            topic_name = topics[topic_id].get("name", "Unknown")

            response = (
                f"✅ Manager topic set successfully!\n"
                f"🆔 Topic {topic_id}: {topic_name}\n\n"
                f"💡 Only this topic can use management commands like /create_topic and /clone."
            )

            await self.im_client.send_message(context, response)

        except Exception as e:
            logger.error(f"Error setting manager topic: {e}", exc_info=True)
            await self.im_client.send_message(context, f"❌ Error setting manager topic: {str(e)}")

    async def handle_delete_topic(self, context: MessageContext, args: str):
        """Handle /delete_topic command - delete a topic"""
        try:
            if not self._is_telegram_with_topics():
                await self.im_client.send_message(
                    context, "❌ This command is only available on Telegram with Topics support."
                )
                return

            # Check if executed in manager topic
            if not self._check_manager权限(context):
                await self.im_client.send_message(
                    context, "❌ This command can only be used in the manager topic."
                )
                return

            if not args:
                await self.im_client.send_message(
                    context, "Usage: /delete_topic <topic_id>\nExample: /delete_topic 123"
                )
                return

            topic_id = args.strip()

            # Delete topic
            success = self.topic_manager.delete_topic(context.channel_id, topic_id)

            if not success:
                await self.im_client.send_message(context, f"❌ Failed to delete topic {topic_id}.")
                return

            # Remove from settings
            settings_key = self.controller._get_settings_key(context)
            self.settings_manager.remove_topic_worktree(
                settings_key, context.channel_id, topic_id
            )

            response = f"✅ Deleted topic {topic_id} and its worktree."

            await self.im_client.send_message(context, response)

        except Exception as e:
            logger.error(f"Error deleting topic: {e}", exc_info=True)
            await self.im_client.send_message(context, f"❌ Error deleting topic: {str(e)}")

    async def handle_project_info(self, context: MessageContext, args: str):
        """Handle /project_info command - show current project info"""
        try:
            if not self._is_telegram_with_topics():
                await self.im_client.send_message(
                    context, "❌ This command is only available on Telegram with Topics support."
                )
                return

            if not context.thread_id:
                await self.im_client.send_message(
                    context, "ℹ️ This command must be used in a topic."
                )
                return

            # Get worktree path
            worktree_path = self.topic_manager.get_worktree_for_topic(
                context.channel_id, context.thread_id
            )

            if not worktree_path:
                await self.im_client.send_message(
                    context, "ℹ️ No project found for this topic. Use /clone or /create_topic to set up a project."
                )
                return

            # Get topic info
            topics = self.topic_manager.list_topics(context.channel_id)
            topic_info = topics.get(str(context.thread_id), {})
            project_name = topic_info.get("name", "Unknown")

            # Build response
            lines = [
                f"📋 **Project Information**",
                f"🆔 Topic ID: {context.thread_id}",
                f"📂 Project: {project_name}",
                f"📁 Worktree: {worktree_path}",
            ]

            response = "\n".join(lines)
            await self.im_client.send_message(context, response)

        except Exception as e:
            logger.error(f"Error getting project info: {e}", exc_info=True)
            await self.im_client.send_message(context, f"❌ Error getting project info: {str(e)}")

    async def handle_git_status(self, context: MessageContext, args: str):
        """Handle /git_status command - show git status"""
        try:
            if not self._is_telegram_with_topics():
                await self.im_client.send_message(
                    context, "❌ This command is only available on Telegram with Topics support."
                )
                return

            if not context.thread_id:
                await self.im_client.send_message(
                    context, "ℹ️ This command must be used in a topic."
                )
                return

            # Get worktree path
            worktree_path = self.topic_manager.get_worktree_for_topic(
                context.channel_id, context.thread_id
            )

            if not worktree_path:
                await self.im_client.send_message(
                    context, "ℹ️ No project found for this topic. Use /clone or /create_topic to set up a project."
                )
                return

            # Run git status
            import subprocess
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=worktree_path,
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                await self.im_client.send_message(
                    context, f"❌ Failed to get git status: {result.stderr}"
                )
                return

            output = result.stdout.strip()

            if not output:
                response = "✅ Git status: Clean (no changes)"
            else:
                lines = output.split("\n")
                response = "📊 **Git Status:**\n" + "\n".join(f"• {line}" for line in lines)

            await self.im_client.send_message(context, response)

        except Exception as e:
            logger.error(f"Error getting git status: {e}", exc_info=True)
            await self.im_client.send_message(context, f"❌ Error getting git status: {str(e)}")

