"""Core controller that coordinates between modules and handlers"""

import asyncio
import os
import logging
from typing import Optional, Dict, Any
from config.settings import AppConfig
from modules.im import BaseIMClient, MessageContext, IMFactory
from modules.im.formatters import TelegramFormatter, SlackFormatter
from modules.agent_router import AgentRouter
from modules.agents import AgentService, ClaudeAgent, CodexAgent, OpenCodeAgent
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

        # Consolidated message tracking (system/assistant/toolcall)
        self._consolidated_message_ids: Dict[str, str] = {}
        self._consolidated_message_buffers: Dict[str, str] = {}
        self._consolidated_message_locks: Dict[str, asyncio.Lock] = {}

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
        self.agent_service = AgentService(self)
        self.agent_service.register(ClaudeAgent(self))
        if self.config.codex:
            try:
                self.agent_service.register(CodexAgent(self, self.config.codex))
            except Exception as e:
                logger.error(f"Failed to initialize Codex agent: {e}")
        if self.config.opencode:
            try:
                self.agent_service.register(OpenCodeAgent(self, self.config.opencode))
            except Exception as e:
                logger.error(f"Failed to initialize OpenCode agent: {e}")

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
            on_routing_update=self.handle_routing_update,
            on_routing_modal_update=self.handle_routing_modal_update,
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

    def _get_consolidated_message_key(self, context: MessageContext) -> str:
        settings_key = self._get_settings_key(context)
        thread_key = context.thread_id or context.channel_id
        return f"{settings_key}:{thread_key}"

    def _get_consolidated_message_lock(self, key: str) -> asyncio.Lock:
        if key not in self._consolidated_message_locks:
            self._consolidated_message_locks[key] = asyncio.Lock()
        return self._consolidated_message_locks[key]

    def _get_consolidated_max_chars(self) -> int:
        # Slack max message length is ~40k characters.
        if self.config.platform == "slack":
            return 35000
        # Telegram hard limit is 4096; MarkdownV2 escaping expands.
        if self.config.platform == "telegram":
            return 3200
        return 8000

    def _truncate_consolidated(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        prefix = "…(truncated)…\n\n"
        keep = max(0, max_chars - len(prefix))
        return f"{prefix}{text[-keep:]}"

    def resolve_agent_for_context(self, context: MessageContext) -> str:
        """Unified agent resolution with dynamic override support.

        Priority:
        1. channel_routing.agent_backend (from user_settings.json)
        2. agent_routes.yaml overrides[channel_id]
        3. agent_routes.yaml platform.default
        4. agent_routes.yaml global default
        5. AgentService.default_agent ("claude")
        """
        settings_key = self._get_settings_key(context)

        # Check dynamic override first
        routing = self.settings_manager.get_channel_routing(settings_key)
        if routing and routing.agent_backend:
            # Verify the agent is registered
            if routing.agent_backend in self.agent_service.agents:
                return routing.agent_backend
            else:
                logger.warning(
                    f"Channel routing specifies '{routing.agent_backend}' but agent is not registered, "
                    f"falling back to static routing"
                )

        # Fall back to static routing
        return self.agent_router.resolve(self.config.platform, settings_key)

    def get_opencode_overrides(
        self, context: MessageContext
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Get OpenCode agent, model, and reasoning effort overrides for this channel.

        Returns:
            Tuple of (opencode_agent, opencode_model, opencode_reasoning_effort)
            or (None, None, None) if no overrides.
        """
        settings_key = self._get_settings_key(context)
        routing = self.settings_manager.get_channel_routing(settings_key)
        if routing:
            return (
                routing.opencode_agent,
                routing.opencode_model,
                routing.opencode_reasoning_effort,
            )
        return None, None, None

    async def emit_agent_message(
        self,
        context: MessageContext,
        message_type: str,
        text: str,
        parse_mode: Optional[str] = "markdown",
    ):
        """Centralized dispatch for agent messages.

        - notify: always send immediately
        - result: always send immediately (not hideable)
        - system/assistant/toolcall: consolidate into a single editable message per thread
        """
        if not text or not text.strip():
            return

        canonical_type = self.settings_manager._canonicalize_message_type(
            message_type or ""
        )
        settings_key = self._get_settings_key(context)

        if canonical_type == "notify":
            target_context = self._get_target_context(context)
            await self.im_client.send_message(
                target_context, text, parse_mode=parse_mode
            )
            return

        if canonical_type == "result":
            target_context = self._get_target_context(context)
            await self.im_client.send_message(
                target_context, text, parse_mode=parse_mode
            )
            return

        if canonical_type not in {"system", "assistant", "toolcall"}:
            canonical_type = "assistant"

        if self.settings_manager.is_message_type_hidden(settings_key, canonical_type):
            preview = text if len(text) <= 500 else f"{text[:500]}…"
            logger.info(
                "Skipping %s message for settings %s (hidden). Preview: %s",
                canonical_type,
                settings_key,
                preview,
            )
            return

        consolidated_key = self._get_consolidated_message_key(context)
        lock = self._get_consolidated_message_lock(consolidated_key)

        async with lock:
            chunk = text.strip()
            existing = self._consolidated_message_buffers.get(consolidated_key, "")
            separator = "\n\n---\n\n" if existing else ""
            updated = f"{existing}{separator}{chunk}" if existing else chunk

            updated = self._truncate_consolidated(updated, self._get_consolidated_max_chars())
            self._consolidated_message_buffers[consolidated_key] = updated

            target_context = self._get_target_context(context)
            existing_message_id = self._consolidated_message_ids.get(consolidated_key)
            if existing_message_id:
                try:
                    ok = await self.im_client.edit_message(
                        target_context,
                        existing_message_id,
                        text=updated,
                        parse_mode="markdown",
                    )
                except Exception as err:
                    logger.warning(f"Failed to edit consolidated message: {err}")
                    ok = False
                if ok:
                    return
                self._consolidated_message_ids.pop(consolidated_key, None)

            try:
                new_id = await self.im_client.send_message(
                    target_context, updated, parse_mode="markdown"
                )
                self._consolidated_message_ids[consolidated_key] = new_id
            except Exception as err:
                logger.error(f"Failed to send consolidated message: {err}", exc_info=True)

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

    async def handle_routing_modal_update(
        self,
        user_id: str,
        channel_id: str,
        view: dict,
        action: dict,
    ) -> None:
        """Handle routing modal updates when selections change."""
        try:
            view_id = view.get("id")
            view_hash = view.get("hash")
            if not view_id or not view_hash:
                logger.warning("Routing modal update missing view id/hash")
                return

            resolved_channel_id = channel_id if channel_id else user_id
            context = MessageContext(
                user_id=user_id,
                channel_id=resolved_channel_id,
                platform_specific={},
            )

            settings_key = self._get_settings_key(context)
            current_routing = self.settings_manager.get_channel_routing(settings_key)
            registered_backends = list(self.agent_service.agents.keys())
            current_backend = self.resolve_agent_for_context(context)

            values = view.get("state", {}).get("values", {})
            backend_data = values.get("backend_block", {}).get("backend_select", {})
            selected_backend = backend_data.get("selected_option", {}).get("value")
            if not selected_backend:
                selected_backend = current_backend

            def _selected_value(block_id: str, action_id: str) -> Optional[str]:
                data = values.get(block_id, {}).get(action_id, {})
                return data.get("selected_option", {}).get("value")

            oc_agent = _selected_value("opencode_agent_block", "opencode_agent_select")
            if oc_agent == "__default__":
                oc_agent = None

            oc_model = _selected_value("opencode_model_block", "opencode_model_select")
            if oc_model == "__default__":
                oc_model = None

            oc_reasoning = _selected_value(
                "opencode_reasoning_block", "opencode_reasoning_select"
            )
            if oc_reasoning == "__default__":
                oc_reasoning = None

            opencode_agents = []
            opencode_models = {}
            opencode_default_config = {}

            if "opencode" in registered_backends:
                try:
                    opencode_agent = self.agent_service.agents.get("opencode")
                    if opencode_agent and hasattr(opencode_agent, "_get_server"):
                        server = await opencode_agent._get_server()
                        await server.ensure_running()
                        cwd = self.get_cwd(context)
                        opencode_agents = await server.get_available_agents(cwd)
                        opencode_models = await server.get_available_models(cwd)
                        opencode_default_config = await server.get_default_config(cwd)
                except Exception as e:
                    logger.warning(f"Failed to fetch OpenCode data: {e}")

            if hasattr(self.im_client, "update_routing_modal"):
                await self.im_client.update_routing_modal(
                    view_id=view_id,
                    view_hash=view_hash,
                    channel_id=resolved_channel_id,
                    registered_backends=registered_backends,
                    current_backend=current_backend,
                    current_routing=current_routing,
                    opencode_agents=opencode_agents,
                    opencode_models=opencode_models,
                    opencode_default_config=opencode_default_config,
                    selected_backend=selected_backend,
                    selected_opencode_agent=oc_agent,
                    selected_opencode_model=oc_model,
                    selected_opencode_reasoning=oc_reasoning,
                )
        except Exception as e:
            logger.error(f"Error updating routing modal: {e}", exc_info=True)

    # Routing update handler (for Slack modal)
    async def handle_routing_update(
        self,
        user_id: str,
        channel_id: str,
        backend: str,
        opencode_agent: Optional[str],
        opencode_model: Optional[str],
        opencode_reasoning_effort: Optional[str] = None,
    ):
        """Handle routing update submission (from Slack modal)"""
        from modules.settings_manager import ChannelRouting

        try:
            # Create routing object
            routing = ChannelRouting(
                agent_backend=backend,
                opencode_agent=opencode_agent,
                opencode_model=opencode_model,
                opencode_reasoning_effort=opencode_reasoning_effort,
            )

            # Get settings key
            settings_key = channel_id if channel_id else user_id

            # Save routing
            self.settings_manager.set_channel_routing(settings_key, routing)

            # Build confirmation message
            parts = [f"Backend: **{backend}**"]
            if backend == "opencode":
                if opencode_agent:
                    parts.append(f"Agent: **{opencode_agent}**")
                if opencode_model:
                    parts.append(f"Model: **{opencode_model}**")
                if opencode_reasoning_effort:
                    parts.append(f"Reasoning Effort: **{opencode_reasoning_effort}**")

            # Create context for confirmation message
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id if channel_id else user_id,
                platform_specific={},
            )

            await self.im_client.send_message(
                context,
                f"✅ Agent routing updated!\n" + "\n".join(parts),
                parse_mode="markdown",
            )

            logger.info(
                f"Routing updated for {settings_key}: backend={backend}, "
                f"agent={opencode_agent}, model={opencode_model}"
            )

        except Exception as e:
            logger.error(f"Error updating routing: {e}")
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id if channel_id else user_id,
                platform_specific={},
            )
            await self.im_client.send_message(
                context, f"❌ Failed to update routing: {str(e)}"
            )

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

        try:
            # Run the IM client (blocking)
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

        # Stop OpenCode server if running
        try:
            from modules.agents.opencode_agent import OpenCodeServerManager
            OpenCodeServerManager.stop_instance_sync()
        except Exception as e:
            logger.debug(f"OpenCode server cleanup skipped: {e}")

        logger.info("Controller cleanup (sync) complete")
