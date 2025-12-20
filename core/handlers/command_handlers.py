"""Command handlers for bot commands like /start, /clear, /cwd, etc."""

import os
import uuid
import logging
from typing import Optional, Dict, Any

from telegram.helpers import escape_markdown
from modules.agents import AgentRequest, get_agent_display_name
from modules.im import MessageContext, InlineKeyboard, InlineButton
from modules.topic_manager import RepositoryExistsError

logger = logging.getLogger(__name__)


class CommandHandlers:
    """Handles all bot command operations"""

    def __init__(self, controller):
        """Initialize with reference to main controller"""
        self.controller = controller
        self.config = controller.config
        self.im_client = controller.im_client
        self.message_handler = controller.message_handler
        self.session_manager = controller.session_manager
        self.settings_manager = controller.settings_manager
        # Get reference to session_handler which has topic_manager
        self.session_handler = controller.session_handler
        self.topic_manager = self.session_handler.topic_manager
        # Cache pending /newtask selections (token -> data)
        self.pending_newtask_requests: Dict[str, Dict[str, Any]] = {}
        # Cache pending /newtask branch selections (token -> data)
        self.pending_newtask_branch_requests: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _escape_md_v2(text: str) -> str:
        """Escape user-provided text for Telegram MarkdownV2."""
        try:
            return escape_markdown(text, version=2)
        except Exception:
            return text

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
                    formatter.format_text("/list_repo - List cloned repositories"),
                    formatter.format_text("/newtask <需求> - 创建新话题并初始化 worktree (管理话题)"),
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

        # General forum topic (thread_id == "1") 始终视为管理话题
        if context.thread_id == "1":
            return True

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
        """Handle /clone command - clone repository into chat workspace"""
        progress_message_id: Optional[str] = None
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

            # Notify user before starting clone
            progress_message_id = await self.im_client.send_message(
                context, "⏳ 正在克隆仓库，请稍等...", parse_mode="plain"
            )

            # Clone project only (worktree will be created by /newtask)
            main_repo_path, _, _, _ = self.topic_manager.clone_project(
                chat_id=context.channel_id,
                git_url=git_url,
            )

            response = (
                f"✅ 已克隆仓库\n"
                f"🔗 Repo: {git_url}\n"
                f"📂 Path: {main_repo_path}\n\n"
                f"💡 在管理话题用 /newtask <需求> 选择该仓库，机器人会自动创建新话题和 worktree。"
            )

            await self.im_client.send_message(context, response, reply_to=progress_message_id, parse_mode="plain")

        except RepositoryExistsError as e:
            logger.info(f"Clone skipped, repo exists: {e.path}")
            await self.im_client.send_message(
                context,
                f"ℹ️ 仓库已存在，无需重新克隆。\n📂 Path: {e.path}",
                reply_to=progress_message_id,
                parse_mode="plain",
            )
        except ValueError as e:
            logger.error(f"Error cloning repository: {e}")
            await self.im_client.send_message(context, f"❌ Failed to clone repository: {str(e)}", parse_mode="plain")
        except Exception as e:
            logger.error(f"Unexpected error cloning repository: {e}", exc_info=True)
            await self.im_client.send_message(context, f"❌ Unexpected error: {str(e)}", parse_mode="plain")

    async def handle_newtask(self, context: MessageContext, args: str):
        """Handle /newtask command - create new topic/worktree for a repo"""
        try:
            if not self._is_telegram_with_topics():
                await self.im_client.send_message(
                    context, "❌ This command is only available on Telegram with Topics support."
                )
                return

            if not self._check_manager权限(context):
                await self.im_client.send_message(
                    context, "❌ This command can only be used in the manager topic."
                )
                return

            task_desc = args.strip()
            if not task_desc:
                await self.im_client.send_message(
                    context, "用法：/newtask <需求>\n示例：/newtask 更新 README", parse_mode="plain"
                )
                return
            task_desc = " ".join(task_desc.split())

            repos = self.topic_manager.list_repositories(context.channel_id)

            if not repos:
                await self.im_client.send_message(
                    context, "📭 还没有可用仓库，请先用 /clone <git_url> 克隆仓库。", parse_mode="plain"
                )
                return

            # Only one repo, create directly
            if len(repos) == 1:
                repo_name, repo_info = next(iter(repos.items()))
                await self._create_topic_for_task(context, task_desc, repo_name, repo_info)
                return

            # Multiple repos: ask user to pick via inline keyboard
            buttons = []
            for repo_name, repo_info in repos.items():
                token = uuid.uuid4().hex[:12]
                self.pending_newtask_requests[token] = {
                    "chat_id": context.channel_id,
                    "user_id": context.user_id,
                    "task_desc": task_desc,
                    "repo_name": repo_name,
                    "repo_info": repo_info,
                }
                buttons.append([InlineButton(text=repo_name, callback_data=f"newtask_repo:{token}")])

            prompt = f"请选择要为任务创建新话题的仓库：\n📝 需求：{task_desc}"
            keyboard = InlineKeyboard(buttons=buttons)
            await self.im_client.send_message_with_buttons(context, prompt, keyboard)

        except Exception as e:
            logger.error(f"Error creating new task: {e}", exc_info=True)
            await self.im_client.send_message(context, f"❌ Error creating new task: {str(e)}", parse_mode="plain")

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

    async def _send_branch_selection_message(
        self,
        context: MessageContext,
        token: str,
        page: int = 1,
        items_per_page: int = 10,
    ):
        """Send branch selection message with clickable links"""
        data = self.pending_newtask_branch_requests.get(token)
        if not data:
            return

        branches = data.get("branches", [])
        total_pages = (len(branches) + items_per_page - 1) // items_per_page

        # Get branches for this page
        start_idx = (page - 1) * items_per_page
        end_idx = min(start_idx + items_per_page, len(branches))
        page_branches = branches[start_idx:end_idx]

        # Build message text
        lines = [
            f"📝 需求：{data['task_desc']}",
            f"📚 仓库：{data['repo_name']}",
            "",
            "请选择要基于的分支（点击链接或直接输入分支名/编号）：",
            ""
        ]

        # Add branch list
        for i, branch in enumerate(page_branches, start=start_idx + 1):
            branch_token = uuid.uuid4().hex[:12]
            self.pending_newtask_branch_requests[branch_token] = {
                "chat_id": data["chat_id"],
                "user_id": data["user_id"],
                "task_desc": data["task_desc"],
                "repo_name": data["repo_name"],
                "repo_info": data["repo_info"],
                "source_branch": branch,
            }
            lines.append(f"{i}. [{branch}](newtask_branch:{branch_token})")

        # Add pagination info
        if total_pages > 1:
            lines.append("")
            lines.append(f"📄 第 {page} 页，共 {total_pages} 页")

            # Add pagination buttons
            buttons = []
            if page > 1:
                prev_token = uuid.uuid4().hex[:12]
                self.pending_newtask_branch_requests[prev_token] = {
                    "chat_id": data["chat_id"],
                    "user_id": data["user_id"],
                    "task_desc": data["task_desc"],
                    "repo_name": data["repo_name"],
                    "repo_info": data["repo_info"],
                    "page": page - 1,
                }
                buttons.append([InlineButton(text="⬅️ 上一页", callback_data=f"newtask_branch_page:{prev_token}")])

            if page < total_pages:
                next_token = uuid.uuid4().hex[:12]
                self.pending_newtask_branch_requests[next_token] = {
                    "chat_id": data["chat_id"],
                    "user_id": data["user_id"],
                    "task_desc": data["task_desc"],
                    "repo_name": data["repo_name"],
                    "repo_info": data["repo_info"],
                    "page": page + 1,
                }
                buttons.append([InlineButton(text="➡️ 下一页", callback_data=f"newtask_branch_page:{next_token}")])

            if buttons:
                keyboard = InlineKeyboard(buttons=buttons)
                message_text = "\n".join(lines)
                await self.im_client.send_message_with_buttons(context, message_text, keyboard)
            else:
                message_text = "\n".join(lines)
                await self.im_client.send_message(context, message_text, parse_mode="MarkdownV2")
        else:
            message_text = "\n".join(lines)
            await self.im_client.send_message(context, message_text, parse_mode="MarkdownV2")

    async def handle_newtask_branch_callback(self, context: MessageContext, token: str):
        """Handle branch selection for /newtask"""
        try:
            data = self.pending_newtask_branch_requests.pop(token, None)

            if not data:
                await self.im_client.send_message(context, "⚠️ 该分支选择已失效，请重新使用 /newtask。", parse_mode="plain")
                return

            if data.get("chat_id") != context.channel_id:
                await self.im_client.send_message(context, "⚠️ 该选项不属于当前会话。", parse_mode="plain")
                return

            if data.get("user_id") and data["user_id"] != context.user_id:
                await self.im_client.send_message(context, "⚠️ 仅任务发起人可以选择分支。", parse_mode="plain")
                return

            # Clean up other pending selections for this user/chat
            tokens_to_remove = [
                key
                for key, value in self.pending_newtask_branch_requests.items()
                if value.get("user_id") == context.user_id
                and value.get("chat_id") == context.channel_id
            ]
            for key in tokens_to_remove:
                self.pending_newtask_branch_requests.pop(key, None)

            source_branch = data.get("source_branch")
            if not source_branch:
                await self.im_client.send_message(context, "⚠️ 未选择分支。", parse_mode="plain")
                return

            await self._create_topic_for_task(
                context,
                data["task_desc"],
                data["repo_name"],
                data["repo_info"],
                source_branch=source_branch,
            )

        except Exception as e:
            logger.error(f"Error handling newtask branch callback: {e}", exc_info=True)

    async def handle_newtask_branch_callback_direct(
        self,
        context: MessageContext,
        token: str,
        selected_branch: str,
    ):
        """Handle branch selection via direct input (not callback)"""
        try:
            data = self.pending_newtask_branch_requests.get(token)
            if not data:
                await self.im_client.send_message(context, "⚠️ 该分支选择已失效，请重新使用 /newtask。", parse_mode="plain")
                return

            if data.get("chat_id") != context.channel_id:
                await self.im_client.send_message(context, "⚠️ 该选项不属于当前会话。", parse_mode="plain")
                return

            if data.get("user_id") and data["user_id"] != context.user_id:
                await self.im_client.send_message(context, "⚠️ 仅任务发起人可以选择分支。", parse_mode="plain")
                return

            # Clean up other pending selections for this user/chat
            tokens_to_remove = [
                key
                for key, value in self.pending_newtask_branch_requests.items()
                if value.get("user_id") == context.user_id
                and value.get("chat_id") == context.channel_id
            ]
            for key in tokens_to_remove:
                self.pending_newtask_branch_requests.pop(key, None)

            # Use the selected branch
            await self._create_topic_for_task(
                context,
                data["task_desc"],
                data["repo_name"],
                data["repo_info"],
                source_branch=selected_branch,
            )

        except Exception as e:
            logger.error(f"Error handling newtask branch callback direct: {e}", exc_info=True)

    async def handle_newtask_branch_page_callback(self, context: MessageContext, token: str):
        """Handle pagination for branch selection"""
        try:
            data = self.pending_newtask_branch_requests.get(token)
            if not data:
                await self.im_client.send_message(context, "⚠️ 分页信息已失效，请重新使用 /newtask。", parse_mode="plain")
                return

            if data.get("chat_id") != context.channel_id:
                await self.im_client.send_message(context, "⚠️ 该选项不属于当前会话。", parse_mode="plain")
                return

            if data.get("user_id") and data["user_id"] != context.user_id:
                await self.im_client.send_message(context, "⚠️ 仅任务发起人可以翻页。", parse_mode="plain")
                return

            page = data.get("page", 1)
            original_token = None
            for tok, val in self.pending_newtask_branch_requests.items():
                if val.get("chat_id") == data["chat_id"] and val.get("user_id") == data["user_id"] and "branches" in val:
                    original_token = tok
                    break

            if original_token:
                # Remove the pagination token
                self.pending_newtask_branch_requests.pop(token, None)
                # Send new page
                await self._send_branch_selection_message(context, original_token, page)

        except Exception as e:
            logger.error(f"Error handling newtask branch page callback: {e}", exc_info=True)

    async def handle_newtask_callback(self, context: MessageContext, token: str):
        """Handle repo selection for /newtask"""
        try:
            data = self.pending_newtask_requests.pop(token, None)

            if not data:
                await self.im_client.send_message(context, "⚠️ 该任务选择已失效，请重新使用 /newtask。", parse_mode="plain")
                return

            if data.get("chat_id") != context.channel_id:
                await self.im_client.send_message(context, "⚠️ 该选项不属于当前会话。", parse_mode="plain")
                return

            if data.get("user_id") and data["user_id"] != context.user_id:
                await self.im_client.send_message(context, "⚠️ 仅任务发起人可以选择仓库。", parse_mode="plain")
                return

            # Clean up other pending selections for this user/chat to avoid duplicates
            tokens_to_remove = [
                key
                for key, value in self.pending_newtask_requests.items()
                if value.get("user_id") == context.user_id
                and value.get("chat_id") == context.channel_id
            ]
            for key in tokens_to_remove:
                self.pending_newtask_requests.pop(key, None)

            # Get available branches for the selected repository
            repo_path = data["repo_info"].get("path")
            if not repo_path:
                await self.im_client.send_message(context, f"❌ 仓库路径不存在，无法获取分支列表。", parse_mode="plain")
                return

            try:
                import subprocess
                result = subprocess.run(
                    ["git", "branch", "-a", "--format=%(refname:short)"],
                    cwd=repo_path,
                    check=True,
                    capture_output=True,
                    text=True
                )
                branches = []
                for line in result.stdout.splitlines():
                    branch = line.strip()
                    # Filter out HEAD and duplicate entries
                    if branch and branch != "HEAD" and branch not in branches:
                        branches.append(branch)

                # Prioritize common branches (both local and remote)
                priority = [
                    "main", "master", "develop", "dev",
                    "origin/main", "origin/master", "origin/develop", "origin/dev"
                ]
                prioritized_branches = []
                for p in priority:
                    if p in branches:
                        prioritized_branches.append(p)
                # Add remaining branches
                for b in branches:
                    if b not in prioritized_branches:
                        prioritized_branches.append(b)

                # If no branches found, use default
                if not prioritized_branches:
                    prioritized_branches = ["main", "master"]

                # Store branches in pending requests for input handling
                branch_token = uuid.uuid4().hex[:12]
                self.pending_newtask_branch_requests[branch_token] = {
                    "chat_id": context.channel_id,
                    "user_id": context.user_id,
                    "task_desc": data["task_desc"],
                    "repo_name": data["repo_name"],
                    "repo_info": data["repo_info"],
                    "branches": prioritized_branches,
                    "page": 1,
                }

                # Send branch selection message
                await self._send_branch_selection_message(context, branch_token, 1)

            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to get branches: {e}")
                # Fallback: create with default branch
                await self._create_topic_for_task(
                    context,
                    data["task_desc"],
                    data["repo_name"],
                    data["repo_info"],
                )

        except Exception as e:
            logger.error(f"Error handling newtask callback: {e}", exc_info=True)
            # await self.im_client.send_message(context, f"❌ Error processing selection: {str(e)}", parse_mode="plain")

    async def _create_topic_for_task(
        self,
        context: MessageContext,
        task_desc: str,
        repo_name: str,
        repo_info: Dict[str, Any],
        source_branch: Optional[str] = None,
    ):
        """Create a new Telegram topic and git worktree for the selected repo"""
        git_url = repo_info.get("git_url")
        if not git_url:
            await self.im_client.send_message(
                context,
                f"❌ 仓库 {repo_name} 缺少 remote.origin.url，无法创建 worktree。",
            )
            return

        # Compose topic name within Telegram limits (1-128 chars)
        topic_title = f"{task_desc} | {repo_name}"
        topic_title = topic_title.replace("\n", " ")
        topic_title = topic_title[:120]

        # Create forum topic
        try:
            thread_id = await self.im_client.create_topic(int(context.channel_id), topic_title)
        except Exception as e:
            logger.error(f"Failed to create forum topic: {e}", exc_info=True)
            safe_error = self._escape_md_v2(str(e))
            await self.im_client.send_message(context, f"❌ 创建话题失败：{safe_error}")
            return

        # Create worktree for the new topic
        try:
            main_repo_path, worktree_path, worktree_branch, actual_source_branch = self.topic_manager.clone_project(
                chat_id=context.channel_id,
                git_url=git_url,
                project_name=repo_name,
                topic_id=thread_id,
                source_branch=source_branch,
            )
        except Exception as e:
            logger.error(f"Failed to prepare worktree: {e}", exc_info=True)
            safe_error = self._escape_md_v2(str(e))
            await self.im_client.send_message(context, f"❌ 创建工作区失败：{safe_error}")
            return

        # Persist mapping
        settings_key = self.controller._get_settings_key(context)
        self.settings_manager.set_topic_worktree(
            settings_key, context.channel_id, thread_id, worktree_path
        )

        # Get the branch info for display
        worktree_branch = worktree_branch or "unknown"
        display_source_branch = actual_source_branch or source_branch or "unknown"

        # Send confirmation in manager topic
        safe_task_desc = self._escape_md_v2(task_desc)
        await self.im_client.send_message(
            context,
            (
                "✅ 已创建新话题并准备工作区。\n"
                f"🧵 Topic ID: `{thread_id}`\n"
                f"📝 需求: {safe_task_desc}\n"
                f"📚 仓库: `{repo_name}`\n"
                f"🌿 工作分支: `{worktree_branch}`\n"
                f"📌 源分支: `{display_source_branch}`\n"
                f"📁 Worktree: `{worktree_path}`"
            )
        )

        # Send detail message inside the new topic
        new_topic_context = MessageContext(
            user_id=context.user_id,
            channel_id=context.channel_id,
            thread_id=thread_id,
            platform_specific=context.platform_specific,
        )
        await self.im_client.send_message(
            new_topic_context,
            (
                f"🎯 任务: {safe_task_desc}\n"
                f"📚 仓库: `{repo_name}`\n"
                f"📂 主仓库: `{main_repo_path}`\n"
                f"🌿 工作分支: `{worktree_branch}`\n"
                f"📌 源分支: `{display_source_branch}`\n"
                f"📁 Worktree: `{worktree_path}`\n\n"
                "现在可以在该话题中开始协作啦～"
            )
        )

        # Kick off AI with the task description in the new topic
        try:
            kickoff_context = MessageContext(
                user_id=context.user_id,
                channel_id=context.channel_id,
                thread_id=thread_id,
                platform_specific=context.platform_specific,
            )
            await self.message_handler.handle_user_message(kickoff_context, task_desc)
        except Exception as e:
            logger.error(f"Failed to kickoff AI message: {e}", exc_info=True)

    async def handle_list_repo(self, context: MessageContext, args: str):
        """Handle /list_repo command - list all cloned repositories for the chat"""
        try:
            if not self._is_telegram_with_topics():
                await self.im_client.send_message(
                    context, "❌ This command is only available on Telegram with Topics support."
                )
                return

            if not self._check_manager权限(context):
                await self.im_client.send_message(
                    context, "❌ This command can only be used in the manager topic."
                )
                return

            repos = self.topic_manager.list_repositories(context.channel_id)

            if not repos:
                await self.im_client.send_message(context, "📭 还没有克隆任何仓库。使用 /clone <git_url> 进行克隆。")
                return

            lines = ["📚 已克隆的仓库：\n"]
            for name, info in repos.items():
                git_url = info.get("git_url")
                lines.append(f"• {name}\n  📂 {info.get('path')}")
                if git_url:
                    lines.append(f"  🔗 {git_url}")

            await self.im_client.send_message(context, "\n".join(lines))

        except Exception as e:
            logger.error(f"Error listing repositories: {e}", exc_info=True)
            await self.im_client.send_message(context, f"❌ Error listing repositories: {str(e)}")

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

            # 无参数且当前在话题里，弹出确认按钮删除当前话题
            if not args and context.thread_id:
                topic_id = context.thread_id

                topics = self.topic_manager.list_topics(context.channel_id)
                topic_info = topics.get(str(topic_id), {})
                topic_name = topic_info.get("name", "Unknown")
                worktree_path = self.topic_manager.get_worktree_for_topic(
                    context.channel_id, topic_id
                )

                lines = [
                    "⚠️ 确认删除当前话题及工作区？",
                    f"🧵 Topic ID: {topic_id}",
                    f"📂 项目: {topic_name}",
                ]
                if worktree_path:
                    lines.append(f"📁 Worktree: {worktree_path}")

                keyboard = InlineKeyboard(
                    buttons=[
                        [
                            InlineButton(
                                text="✅ 删除本话题和工作区",
                                callback_data=f"delete_topic_confirm:{context.channel_id}:{topic_id}:yes",
                            ),
                            InlineButton(
                                text="❌ 取消",
                                callback_data=f"delete_topic_confirm:{context.channel_id}:{topic_id}:no",
                            ),
                        ]
                    ]
                )

                await self.im_client.send_message_with_buttons(
                    context, "\n".join(lines), keyboard
                )
                return

            # 以下逻辑：管理话题在命令中指定 topic_id
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
            # Clear manager topic mapping if this was the manager topic
            self.settings_manager.clear_manager_topic(
                settings_key, context.channel_id, topic_id
            )

            response = f"✅ Deleted topic {topic_id} and its worktree."

            await self.im_client.send_message(context, response)

        except Exception as e:
            logger.error(f"Error deleting topic: {e}", exc_info=True)
            await self.im_client.send_message(context, f"❌ Error deleting topic: {str(e)}")

    async def handle_delete_topic_confirmation(
        self, context: MessageContext, chat_id: str, topic_id: str, confirmed: bool
    ):
        """Process inline confirmation for deleting the current topic"""
        try:
            if not self._is_telegram_with_topics():
                return

            # 仅允许在对应话题的回调中操作
            if not context.thread_id or str(context.thread_id) != str(topic_id):
                await self.im_client.send_message(
                    context, "⚠️ 请在要删除的话题内确认。"
                )
                return

            if not confirmed:
                await self.im_client.send_message(context, "操作已取消，不删除该话题。", parse_mode="plain")
                return

            success = self.topic_manager.delete_topic(chat_id, topic_id)
            settings_key = self.controller._get_settings_key(context)
            self.settings_manager.remove_topic_worktree(
                settings_key, chat_id, topic_id
            )
            self.settings_manager.clear_manager_topic(
                settings_key, chat_id, topic_id
            )

            if success:
                await self.im_client.send_message(
                    context, f"✅ 已删除话题 {topic_id} 及对应工作区。", parse_mode="plain"
                )
            else:
                await self.im_client.send_message(
                    context, f"ℹ️ 本地未找到话题 {topic_id} 的记录，已清理设置绑定。", parse_mode="plain"
                )
        except Exception as e:
            logger.error(f"Error in delete topic confirmation: {e}", exc_info=True)
            await self.im_client.send_message(
                context, f"❌ 删除失败：{str(e)}", parse_mode="plain"
            )

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
