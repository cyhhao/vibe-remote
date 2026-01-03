"""Command handlers for bot commands like /start, /clear, /cwd, etc."""

import os
import logging
from typing import Optional
from modules.agents import AgentRequest, get_agent_display_name
from modules.agents.base import get_git_branch
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
                "",
                formatter.format_bold("How it works:"),
                formatter.format_text(
                    f"• Send any message and it's immediately sent to {agent_display_name}"
                ),
                formatter.format_text(
                    "• Each chat maintains its own conversation context"
                ),
                formatter.format_text("• Use /clear to reset the conversation"),
            ]

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

    async def handle_create_pr(self, context: MessageContext, args: str = ""):
        """Handle Create PR button - send PR creation request to the agent in thread"""
        try:
            session_handler = self.controller.session_handler
            base_session_id, working_path, composite_key = (
                session_handler.get_session_info(context)
            )
            settings_key = self.controller._get_settings_key(context)

            # Validate thread-branch association
            is_valid, current_branch, tracked_branch = self._validate_thread_branch(
                context, working_path
            )
            if not is_valid:
                await self._send_branch_mismatch_error(
                    context, current_branch, tracked_branch
                )
                return

            agent_name = self.controller.agent_router.resolve(
                self.config.platform, settings_key
            )

            # PR message with branch check and rebase instructions
            pr_message = (
                "Before creating a PR, please:\n"
                "1. Check the current branch with `git branch`\n"
                "2. Fetch latest main branch with `git fetch origin main`\n"
                "3. Rebase onto main with `git rebase origin/main`\n"
                "4. If rebase has conflicts, resolve them\n"
                "5. Push the changes with `git push -f` if needed\n"
                "6. Create a pull request using `gh pr create`"
            )

            request = AgentRequest(
                context=context,
                message=pr_message,
                working_path=working_path,
                base_session_id=base_session_id,
                composite_session_id=composite_key,
                settings_key=settings_key,
                is_pr_creation=True,
            )

            # Mark this session as creating PR
            self.controller.pending_pr_sessions[composite_key] = True

            # Send acknowledgment in thread
            target_context = self._get_target_context(context)
            await self.im_client.send_message(
                target_context, "🚀 Checking branch and preparing pull request..."
            )

            # Route to agent service
            await self.controller.agent_service.handle_message(agent_name, request)

        except Exception as e:
            logger.error(f"Error creating PR: {e}", exc_info=True)
            target_context = self._get_target_context(context)
            await self.im_client.send_message(
                target_context, f"❌ Error creating pull request: {str(e)}"
            )

    async def handle_codex_review(self, context: MessageContext, args: str = ""):
        """Handle Codex Review button - request code review from Codex agent"""
        try:
            session_handler = self.controller.session_handler
            base_session_id, working_path, composite_key = (
                session_handler.get_session_info(context)
            )
            settings_key = self.controller._get_settings_key(context)

            # Always use codex agent for review
            agent_name = "codex"

            # Review message
            review_message = (
                "Please review the code changes in this branch. "
                "Check for:\n"
                "1. Code quality and best practices\n"
                "2. Potential bugs or issues\n"
                "3. Security concerns\n"
                "4. Performance considerations\n"
                "5. Any improvements that could be made"
            )

            request = AgentRequest(
                context=context,
                message=review_message,
                working_path=working_path,
                base_session_id=base_session_id,
                composite_session_id=composite_key,
                settings_key=settings_key,
            )

            # Send acknowledgment in thread
            target_context = self._get_target_context(context)
            await self.im_client.send_message(
                target_context, "🔍 Starting Codex code review..."
            )

            # Route to codex agent
            await self.controller.agent_service.handle_message(agent_name, request)

        except Exception as e:
            logger.error(f"Error starting Codex review: {e}", exc_info=True)
            target_context = self._get_target_context(context)
            await self.im_client.send_message(
                target_context, f"❌ Error starting code review: {str(e)}"
            )

    async def handle_merge_pr(self, context: MessageContext, args: str = ""):
        """Handle Merge PR button - merge the PR"""
        try:
            session_handler = self.controller.session_handler
            base_session_id, working_path, composite_key = (
                session_handler.get_session_info(context)
            )
            settings_key = self.controller._get_settings_key(context)

            # Validate thread-branch association
            is_valid, current_branch, tracked_branch = self._validate_thread_branch(
                context, working_path
            )
            if not is_valid:
                await self._send_branch_mismatch_error(
                    context, current_branch, tracked_branch
                )
                return

            agent_name = self.controller.agent_router.resolve(
                self.config.platform, settings_key
            )

            # Merge PR message
            merge_message = (
                "Please merge the PR that was just created. "
                "Use `gh pr merge --squash --delete-branch` to merge with squash and delete the branch."
            )

            request = AgentRequest(
                context=context,
                message=merge_message,
                working_path=working_path,
                base_session_id=base_session_id,
                composite_session_id=composite_key,
                settings_key=settings_key,
            )

            # Send acknowledgment in thread
            target_context = self._get_target_context(context)
            await self.im_client.send_message(
                target_context, "✅ Merging pull request..."
            )

            # Route to agent service
            await self.controller.agent_service.handle_message(agent_name, request)

            # After successful merge, clear the thread-branch association
            # (the branch will be deleted, so no point tracking it)
            self.settings_manager.clear_thread_branch(settings_key, base_session_id)
            logger.info(
                f"Cleared thread-branch association for {base_session_id} after PR merge"
            )

        except Exception as e:
            logger.error(f"Error merging PR: {e}", exc_info=True)
            target_context = self._get_target_context(context)
            await self.im_client.send_message(
                target_context, f"❌ Error merging pull request: {str(e)}"
            )

    async def handle_close_pr(self, context: MessageContext, args: str = ""):
        """Handle Close PR button - close the PR without merging"""
        try:
            session_handler = self.controller.session_handler
            base_session_id, working_path, composite_key = (
                session_handler.get_session_info(context)
            )
            settings_key = self.controller._get_settings_key(context)

            # Validate thread-branch association
            is_valid, current_branch, tracked_branch = self._validate_thread_branch(
                context, working_path
            )
            if not is_valid:
                await self._send_branch_mismatch_error(
                    context, current_branch, tracked_branch
                )
                return

            agent_name = self.controller.agent_router.resolve(
                self.config.platform, settings_key
            )

            # Close PR message
            close_message = (
                "Please close the PR that was just created without merging. "
                "Use `gh pr close` to close the PR."
            )

            request = AgentRequest(
                context=context,
                message=close_message,
                working_path=working_path,
                base_session_id=base_session_id,
                composite_session_id=composite_key,
                settings_key=settings_key,
            )

            # Send acknowledgment in thread
            target_context = self._get_target_context(context)
            await self.im_client.send_message(
                target_context, "❌ Closing pull request..."
            )

            # Route to agent service
            await self.controller.agent_service.handle_message(agent_name, request)

            # After closing PR, clear the thread-branch association
            # so the thread can work on a new branch if needed
            self.settings_manager.clear_thread_branch(settings_key, base_session_id)
            logger.info(
                f"Cleared thread-branch association for {base_session_id} after PR close"
            )

        except Exception as e:
            logger.error(f"Error closing PR: {e}", exc_info=True)
            target_context = self._get_target_context(context)
            await self.im_client.send_message(
                target_context, f"❌ Error closing pull request: {str(e)}"
            )

    def _get_target_context(self, context: MessageContext) -> MessageContext:
        """Get target context for sending messages (respects thread replies)"""
        if self.config.platform == "slack" and context.thread_id:
            return MessageContext(
                user_id=context.user_id,
                channel_id=context.channel_id,
                thread_id=context.thread_id,
                platform_specific=context.platform_specific,
            )
        return context

    def _validate_thread_branch(
        self, context: MessageContext, working_path: str
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """Validate that the current git branch matches the thread's tracked branch.

        Returns:
            (is_valid, current_branch, tracked_branch)
            - is_valid: True if branch matches or no branch tracking exists
            - current_branch: The current git branch (or None if not a git repo)
            - tracked_branch: The branch tracked for this thread (or None if not tracked)
        """
        session_handler = self.controller.session_handler
        base_session_id = session_handler.get_base_session_id(context)
        settings_key = self.controller._get_settings_key(context)

        current_branch = get_git_branch(working_path)
        if not current_branch:
            # Not a git repo - allow the action
            return True, None, None

        tracked_branch = self.settings_manager.get_thread_branch(
            settings_key, base_session_id
        )

        if not tracked_branch:
            # No branch tracked for this thread - allow the action
            return True, current_branch, None

        # Validate branch matches
        if current_branch != tracked_branch:
            logger.warning(
                f"Branch mismatch for session {base_session_id}: "
                f"expected {tracked_branch}, got {current_branch}"
            )
            return False, current_branch, tracked_branch

        return True, current_branch, tracked_branch

    async def _send_branch_mismatch_error(
        self, context: MessageContext, current_branch: str, tracked_branch: str
    ):
        """Send an error message when thread-branch mismatch is detected."""
        formatter = self.im_client.formatter
        error_msg = (
            f"❌ Branch mismatch detected!\n\n"
            f"This thread was working on branch {formatter.format_code_inline(tracked_branch)}, "
            f"but the working directory is now on {formatter.format_code_inline(current_branch)}.\n\n"
            f"This can happen when multiple threads work on different branches in the same working directory.\n"
            f"Please ensure the correct branch is checked out before proceeding."
        )
        target_context = self._get_target_context(context)
        await self.im_client.send_message(target_context, error_msg)
