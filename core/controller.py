"""Core controller that coordinates between modules and handlers"""

import asyncio
import os
import logging
import time
from typing import Optional, Dict, Any, List, Tuple
from config.settings import AppConfig
from modules.im import BaseIMClient, MessageContext, IMFactory
from modules.im.formatters import TelegramFormatter, SlackFormatter
from modules.agent_router import AgentRouter
from modules.agents import AgentService, ClaudeAgent, CodexAgent
from modules.claude_client import ClaudeClient
from modules.session_manager import SessionManager
from modules.settings_manager import SettingsManager
from core.handlers import (
    CommandHandlers,
    SessionHandler,
    SettingsHandler,
    MessageHandler,
)

logger = logging.getLogger(__name__)


class Controller:
    """Main controller that coordinates all bot operations"""

    def __init__(self, config: AppConfig):
        """Initialize controller with configuration"""
        self.config = config

        # Session tracking (must be initialized before handlers)
        self.claude_sessions: Dict[str, Any] = {}
        self.receiver_tasks: Dict[str, asyncio.Task] = {}
        self.stored_session_mappings: Dict[str, str] = {}
        # Track sessions that are creating PRs (composite_session_id -> True)
        self.pending_pr_sessions: Dict[str, bool] = {}
        # Track last activity timestamp per session (composite_session_id -> timestamp)
        self.session_last_activity: Dict[str, float] = {}

        # Initialize core modules
        self._init_modules()

        # Initialize handlers
        self._init_handlers()

        # Initialize agents (depends on handlers/session handler)
        self._init_agents()

        # Setup callbacks
        self._setup_callbacks()

        # Background task for cleanup
        self.cleanup_task: Optional[asyncio.Task] = None

        # Flag to track if restart notification has been sent
        self._restart_notified = False

        # Restore session mappings on startup (after handlers are initialized)
        self.session_handler.restore_session_mappings()

    def _init_modules(self):
        """Initialize core modules"""
        # Create IM client with platform-specific formatter
        self.im_client: BaseIMClient = IMFactory.create_client(self.config)

        # Create platform-specific formatter
        if self.config.platform == "telegram":
            formatter = TelegramFormatter()
        elif self.config.platform == "slack":
            formatter = SlackFormatter()
        else:
            logger.warning(
                f"Unknown platform: {self.config.platform}, using Telegram formatter"
            )
            formatter = TelegramFormatter()

        # Inject formatter into clients
        self.im_client.formatter = formatter
        self.claude_client = ClaudeClient(self.config.claude, formatter)

        # Initialize managers
        self.session_manager = SessionManager()
        self.settings_manager = SettingsManager()

        # Agent routing (service initialized later after handlers)
        self.agent_router = AgentRouter.from_file(
            self.config.agent_route_file, platform=self.config.platform
        )

        # Inject settings_manager into SlackBot if it's Slack platform
        if self.config.platform == "slack":
            # Import here to avoid circular dependency
            from modules.im.slack import SlackBot
            if isinstance(self.im_client, SlackBot):
                self.im_client.set_settings_manager(self.settings_manager)
                logger.info("Injected settings_manager into SlackBot for thread tracking")

    def _init_handlers(self):
        """Initialize all handlers with controller reference"""
        # Initialize session_handler first as other handlers depend on it
        self.session_handler = SessionHandler(self)
        self.command_handler = CommandHandlers(self)
        self.settings_handler = SettingsHandler(self)
        self.message_handler = MessageHandler(self)

        # Set cross-references between handlers
        self.message_handler.set_session_handler(self.session_handler)

    def _init_agents(self):
        """Initialize agent implementations (requires handlers ready)."""
        self.agent_service = AgentService(self)
        self.agent_service.register(ClaudeAgent(self))
        if self.config.codex:
            try:
                self.agent_service.register(CodexAgent(self, self.config.codex))
            except Exception as e:
                logger.error(f"Failed to initialize Codex agent: {e}")

    def _setup_callbacks(self):
        """Setup callback connections between modules"""
        # Create command handlers dict
        command_handlers = {
            "start": self.command_handler.handle_start,
            "clear": self.command_handler.handle_clear,
            "cwd": self.command_handler.handle_cwd,
            "set_cwd": self.command_handler.handle_set_cwd,
            "settings": self.settings_handler.handle_settings,
            "stop": self.command_handler.handle_stop,
        }

        # Register callbacks with the IM client
        self.im_client.register_callbacks(
            on_message=self.message_handler.handle_user_message,
            on_command=command_handlers,
            on_callback_query=self.message_handler.handle_callback_query,
            on_settings_update=self.handle_settings_update,
            on_change_cwd=self.handle_change_cwd_submission,
            on_shutdown=self.send_restart_notifications,
        )

    # Utility methods used by handlers

    def get_cwd(self, context: MessageContext) -> str:
        """Get working directory based on context (channel/chat)
        This is the SINGLE source of truth for CWD
        """
        # Get the settings key based on context
        settings_key = self._get_settings_key(context)

        # Get custom CWD from settings
        custom_cwd = self.settings_manager.get_custom_cwd(settings_key)

        # Use custom CWD if available, otherwise use default from .env
        if custom_cwd and os.path.exists(custom_cwd):
            return os.path.abspath(custom_cwd)
        elif custom_cwd:
            logger.warning(f"Custom CWD does not exist: {custom_cwd}, using default")

        # Fall back to default from .env
        default_cwd = self.config.claude.cwd
        if default_cwd:
            return os.path.abspath(os.path.expanduser(default_cwd))

        # Last resort: current directory
        return os.getcwd()

    def _get_settings_key(self, context: MessageContext) -> str:
        """Get settings key based on context"""
        if self.config.platform == "slack":
            # For Slack, always use channel_id as the key
            return context.channel_id
        elif self.config.platform == "telegram":
            # For Telegram groups, use channel_id; for DMs use user_id
            if context.channel_id != context.user_id:
                return context.channel_id
            return context.user_id
        return context.user_id

    def _get_target_context(self, context: MessageContext) -> MessageContext:
        """Get target context for sending messages"""
        if self.im_client.should_use_thread_for_reply() and context.thread_id:
            return MessageContext(
                user_id=context.user_id,
                channel_id=context.channel_id,
                thread_id=context.thread_id,
                message_id=context.message_id,
                platform_specific=context.platform_specific,
            )
        return context

    async def emit_agent_message(
        self,
        context: MessageContext,
        message_type: str,
        text: str,
        parse_mode: str = "markdown",
    ):
        """Centralized dispatch for agent messages with filtering."""
        if not text or not text.strip():
            return
        settings_key = self._get_settings_key(context)
        if (
            message_type != "notify"
            and self.settings_manager.is_message_type_hidden(settings_key, message_type)
        ):
            preview = text if len(text) <= 500 else f"{text[:500]}…"
            logger.info(
                "Skipping %s message for settings %s (hidden). Preview: %s",
                message_type,
                settings_key,
                preview,
            )
            return
        target_context = self._get_target_context(context)
        await self.im_client.send_message(
            target_context, text, parse_mode=parse_mode
        )

    # Settings update handler (for Slack modal)
    async def handle_settings_update(
        self, user_id: str, hidden_message_types: list, channel_id: str = None
    ):
        """Handle settings update (typically from Slack modal)"""
        try:
            # Determine settings key - for Slack, always use channel_id
            if self.config.platform == "slack":
                settings_key = (
                    channel_id if channel_id else user_id
                )  # fallback to user_id if no channel
            else:
                settings_key = channel_id if channel_id else user_id

            # Update settings
            user_settings = self.settings_manager.get_user_settings(settings_key)
            user_settings.hidden_message_types = hidden_message_types

            # Save settings - using the correct method name
            self.settings_manager.update_user_settings(settings_key, user_settings)

            logger.info(
                f"Updated settings for {settings_key}: hidden types = {hidden_message_types}"
            )

            # Create context for sending confirmation (without 'message' field)
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id if channel_id else user_id,
                platform_specific={},
            )

            # Send confirmation
            await self.im_client.send_message(
                context, "✅ Settings updated successfully!"
            )

        except Exception as e:
            logger.error(f"Error updating settings: {e}")
            # Create context for error message (without 'message' field)
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id if channel_id else user_id,
                platform_specific={},
            )
            await self.im_client.send_message(
                context, f"❌ Failed to update settings: {str(e)}"
            )

    # Working directory change handler (for Slack modal)
    async def handle_change_cwd_submission(
        self, user_id: str, new_cwd: str, channel_id: str = None
    ):
        """Handle working directory change submission (from Slack modal) - reuse command handler logic"""
        try:
            # Create context for messages (without 'message' field which doesn't exist in MessageContext)
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id if channel_id else user_id,
                platform_specific={},
            )

            # Reuse the same logic from handle_set_cwd command handler
            await self.command_handler.handle_set_cwd(context, new_cwd.strip())

        except Exception as e:
            logger.error(f"Error changing working directory: {e}")
            # Create context for error message (without 'message' field)
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id if channel_id else user_id,
                platform_specific={},
            )
            await self.im_client.send_message(
                context, f"❌ Failed to change working directory: {str(e)}"
            )

    def mark_session_active(self, composite_key: str) -> None:
        """Mark a session as actively processing (updates last activity timestamp)."""
        self.session_last_activity[composite_key] = time.time()

    def mark_session_idle(self, composite_key: str) -> None:
        """Mark a session as idle (removes from active tracking)."""
        self.session_last_activity.pop(composite_key, None)

    def is_session_recently_active(self, composite_key: str, threshold_seconds: float = 5.0) -> bool:
        """Check if a session has been active within the threshold period."""
        last_activity = self.session_last_activity.get(composite_key)
        if last_activity is None:
            return False
        return (time.time() - last_activity) <= threshold_seconds

    def get_active_sessions(self, activity_threshold_seconds: float = 5.0) -> List[Tuple[str, MessageContext]]:
        """Get all sessions with recent activity (in-progress or recently finished).

        Returns a list of (composite_key, MessageContext) tuples for sessions
        that are currently processing or finished within the threshold period.

        Args:
            activity_threshold_seconds: Sessions active within this many seconds
                are considered "in progress" for restart notifications.
        """
        active_sessions = []

        for composite_key, task in self.receiver_tasks.items():
            # Only consider sessions with recent activity
            if not self.is_session_recently_active(composite_key, activity_threshold_seconds):
                continue

            if task and not task.done():
                # Parse composite key: {base_session_id}:{working_path}
                # base_session_id format: {platform}_{thread_id/channel_id}
                try:
                    parts = composite_key.split(":", 1)
                    if len(parts) != 2:
                        continue

                    base_session_id = parts[0]

                    # Parse platform and ID from base_session_id
                    if base_session_id.startswith("slack_"):
                        thread_id = base_session_id[6:]  # Remove "slack_" prefix
                        # For Slack, we need to find the channel_id from settings
                        # The thread_ts alone is not enough; we need to look it up
                        context = self._find_slack_context_for_thread(thread_id)
                        if context:
                            active_sessions.append((composite_key, context))
                    elif base_session_id.startswith("telegram_"):
                        channel_id = base_session_id[9:]  # Remove "telegram_" prefix
                        context = MessageContext(
                            user_id=channel_id,  # For Telegram, user_id can be same as channel for DMs
                            channel_id=channel_id,
                            thread_id=None,
                        )
                        active_sessions.append((composite_key, context))
                except Exception as e:
                    logger.warning(f"Failed to parse session key {composite_key}: {e}")
                    continue

        return active_sessions

    def _find_slack_context_for_thread(self, thread_id: str) -> Optional[MessageContext]:
        """Find the channel_id for a Slack thread from settings."""
        # Iterate through all user settings to find the channel containing this thread
        for user_id, user_settings in self.settings_manager.settings.items():
            if hasattr(user_settings, 'active_slack_threads') and user_settings.active_slack_threads:
                for channel_id, threads in user_settings.active_slack_threads.items():
                    if thread_id in threads:
                        return MessageContext(
                            user_id=str(user_id),
                            channel_id=channel_id,
                            thread_id=thread_id,
                        )

        # If not found in active threads, try to extract from session mappings
        # This covers cases where the thread might not be in active_slack_threads yet
        for user_id, user_settings in self.settings_manager.settings.items():
            if hasattr(user_settings, 'session_mappings') and user_settings.session_mappings:
                for agent_name, agent_sessions in user_settings.session_mappings.items():
                    for base_session_id in agent_sessions.keys():
                        if base_session_id == f"slack_{thread_id}":
                            # We found the session, but we need channel_id
                            # For Slack, the settings_key (user_id in settings) IS the channel_id
                            return MessageContext(
                                user_id=str(user_id),
                                channel_id=str(user_id),  # In Slack, settings are stored by channel_id
                                thread_id=thread_id,
                            )

        return None

    async def send_restart_notifications(self):
        """Send restart notification to all active sessions."""
        if self._restart_notified:
            logger.info("Restart notifications already sent, skipping")
            return

        active_sessions = self.get_active_sessions()

        if not active_sessions:
            logger.info("No active sessions to notify about restart")
            return

        logger.info(f"Sending restart notifications to {len(active_sessions)} active session(s)")

        notification_text = "🔄 Bot is restarting... Your session will resume shortly."

        for composite_key, context in active_sessions:
            try:
                target_context = self._get_target_context(context)
                await self.im_client.send_message(
                    target_context,
                    notification_text,
                    parse_mode="markdown"
                )
                logger.info(f"Sent restart notification to session {composite_key}")
            except Exception as e:
                logger.warning(f"Failed to send restart notification to {composite_key}: {e}")

        self._restart_notified = True
        logger.info("Restart notifications sent successfully")

    # Main run method
    def run(self):
        """Run the controller"""
        logger.info(
            f"Starting Claude Proxy Controller with {self.config.platform} platform..."
        )

        # 不再创建额外事件循环，避免与 IM 客户端的内部事件循环冲突
        # 清理职责改为：
        # - 仅当收到消息且开启 cleanup_enabled 时，在消息入口清理已完成任务（见 MessageHandler）
        # - 进程退出时做一次同步的 best-effort 取消（不跨循环 await）
        # - 重启通知由 IM 客户端的信号处理器调用 on_shutdown 回调发送

        try:
            # Run the IM client (blocking)
            # The IM client handles SIGTERM/SIGINT and calls our shutdown callback
            self.im_client.run()
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
        except Exception as e:
            logger.error(f"Error in main run loop: {e}", exc_info=True)
        finally:
            # Best-effort 同步清理，避免跨事件循环 await
            self.cleanup_sync()

    async def periodic_cleanup(self):
        """[Deprecated] Periodic cleanup is disabled in favor of safe on-demand cleanup"""
        logger.info("periodic_cleanup is deprecated and not scheduled.")
        return

    def cleanup_sync(self):
        """Best-effort synchronous cleanup without cross-loop awaits"""
        logger.info("Cleaning up controller resources (sync, best-effort)...")

        # Cancel receiver tasks without awaiting (they may belong to other loops)
        try:
            for session_id, task in list(self.receiver_tasks.items()):
                if not task.done():
                    task.cancel()
                # Remove from registry regardless
                del self.receiver_tasks[session_id]
        except Exception as e:
            logger.debug(f"Receiver tasks cleanup skipped due to: {e}")

        # Do not attempt to await SessionHandler cleanup here to avoid cross-loop issues.
        # Active connections will be closed by process exit; mappings are persisted separately.

        # Attempt to call stop if it's a plain function; skip if coroutine to avoid cross-loop awaits
        try:
            stop_attr = getattr(self.im_client, "stop", None)
            if callable(stop_attr):
                import inspect

                if not inspect.iscoroutinefunction(stop_attr):
                    stop_attr()
        except Exception:
            pass

        logger.info("Controller cleanup (sync) complete")
