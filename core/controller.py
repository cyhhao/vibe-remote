"""Core controller that coordinates between modules and handlers"""

import asyncio
import json
import os
import logging
import threading
from typing import Optional, Dict, Any
from config import paths
from modules.im import BaseIMClient, MessageContext, IMFactory
from modules.im.multi import MultiIMClient
from modules.im.formatters import SlackFormatter, DiscordFormatter
from modules.agent_router import AgentRouter
from modules.agents import AgentService, ClaudeAgent, CodexAgent, OpenCodeAgent
from modules.claude_client import ClaudeClient
from modules.session_manager import SessionManager
from modules.settings_manager import SettingsManager, MultiSettingsManager
from core.handlers import (
    CommandHandlers,
    SessionHandler,
    SettingsHandler,
    MessageHandler,
)
from core.message_dispatcher import ConsolidatedMessageDispatcher
from core.update_checker import UpdateChecker
from vibe.i18n import get_supported_languages, t as i18n_t

logger = logging.getLogger(__name__)


class Controller:
    """Main controller that coordinates all bot operations"""

    def __init__(self, config):
        """Initialize controller with configuration"""
        self.config = config
        self._config_mtime: Optional[float] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._im_thread: Optional[threading.Thread] = None
        self._im_run_exception: Optional[BaseException] = None
        self.enabled_platforms = list(getattr(config, "enabled_platforms", lambda: [config.platform])())
        self.primary_platform = getattr(getattr(config, "platforms", None), "primary", config.platform)

        # Session tracking (must be initialized before handlers)
        self.claude_sessions: Dict[str, Any] = {}
        self.receiver_tasks: Dict[str, asyncio.Task] = {}
        self.stored_session_mappings: Dict[str, str] = {}

        # Initialize core modules
        self._init_modules()

        # Initialize handlers
        self._init_handlers()

        # Initialize agents (depends on handlers/session handler)
        self._init_agents()

        # Validate default_backend against registered agents
        self._validate_default_backend()

        # Setup callbacks
        self._setup_callbacks()

        # Consolidated message dispatcher
        self.message_dispatcher = ConsolidatedMessageDispatcher(self)

        # Background task for cleanup
        self.cleanup_task: Optional[asyncio.Task] = None

        # Initialize update checker (use default config if not present)
        from config.v2_config import UpdateConfig

        update_config = getattr(config, "update", None) or UpdateConfig()
        self.update_checker = UpdateChecker(self, update_config)

        # Restore session mappings on startup (after handlers are initialized)
        self.session_handler.restore_session_mappings()

    def _init_modules(self):
        """Initialize core modules"""
        self.im_clients: Dict[str, BaseIMClient] = IMFactory.create_clients(self.config)
        for platform, client in self.im_clients.items():
            client.formatter = self._create_formatter(platform)

        self.im_client = (
            self.im_clients[self.primary_platform]
            if len(self.im_clients) == 1
            else MultiIMClient(self.im_clients, primary_platform=self.primary_platform)
        )
        formatter = self.im_clients[self.primary_platform].formatter
        self.claude_client = ClaudeClient(self.config.claude, formatter)

        # Initialize managers
        self.session_manager = SessionManager()
        self.settings_manager = MultiSettingsManager(self.enabled_platforms, primary_platform=self.primary_platform)
        self.platform_settings_managers = self.settings_manager.managers
        self.sessions = self.settings_manager.sessions

        # Migrate legacy per-channel language into global config
        self._migrate_language_from_settings()

        # Agent routing - use configured default_backend
        default_backend = getattr(self.config, "default_backend", "opencode")
        self.agent_router = AgentRouter.from_file(None, platform=self.primary_platform, default_backend=default_backend)
        for platform in self.enabled_platforms:
            if platform not in self.agent_router.platform_routes:
                self.agent_router.platform_routes[platform] = self.agent_router.platform_routes[self.primary_platform]

        # Inject settings_manager into IM client if supported
        for platform, client in self.im_clients.items():
            self._inject_runtime_dependencies(platform, client)

    def _create_formatter(self, platform: str):
        if platform == "discord":
            return DiscordFormatter()
        if platform == "lark":
            from modules.im.formatters.feishu_formatter import FeishuFormatter

            return FeishuFormatter()
        if platform == "wechat":
            from modules.im.formatters.wechat_formatter import WeChatFormatter

            return WeChatFormatter()
        return SlackFormatter()

    def _inject_runtime_dependencies(self, platform: str, client: BaseIMClient) -> None:
        settings_manager = self.platform_settings_managers[platform]
        setter = getattr(client, "set_settings_manager", None)
        if callable(setter):
            setter(settings_manager)
        controller_setter = getattr(client, "set_controller", None)
        if callable(controller_setter):
            controller_setter(self)
        logger.info("Injected settings_manager and controller into %s client", platform)

    def _get_lang(self) -> str:
        self._refresh_config_from_disk()
        return getattr(self.config, "language", "en")

    def _t(self, key: str, **kwargs) -> str:
        return i18n_t(key, self._get_lang(), **kwargs)

    def _refresh_config_from_disk(self) -> None:
        """Hot-reload mutable message-processing settings from config.json.

        Called on every ``_t()`` invocation (guarded by mtime check).
        Refreshes: language, show_duration, ack_mode, include_user_info,
        reply_enhancements, require_mention (global).
        """
        try:
            config_path = paths.get_config_path()
            if not config_path.exists():
                return
            mtime = config_path.stat().st_mtime
            if self._config_mtime != mtime:
                from config.v2_config import V2Config

                v2_config = V2Config.load()
                self.config.language = v2_config.language
                self.config.show_duration = v2_config.show_duration
                self.config.ack_mode = v2_config.ack_mode
                self.config.include_user_info = v2_config.include_user_info
                self.config.reply_enhancements = v2_config.reply_enhancements

                # Sync global require_mention into the IM client's platform config
                for platform, client in self.im_clients.items():
                    im_cfg = getattr(client, "config", None)
                    if im_cfg is None or not hasattr(im_cfg, "require_mention"):
                        continue
                    if platform == "lark" and v2_config.lark:
                        im_cfg.require_mention = v2_config.lark.require_mention
                    elif platform == "slack":
                        im_cfg.require_mention = v2_config.slack.require_mention
                    elif platform == "discord" and v2_config.discord:
                        im_cfg.require_mention = v2_config.discord.require_mention
                    elif platform == "wechat" and v2_config.wechat:
                        im_cfg.require_mention = v2_config.wechat.require_mention

                self._config_mtime = mtime
        except Exception as err:
            logger.debug("Failed to reload config from disk: %s", err)

    def _migrate_language_from_settings(self) -> None:
        """Persist legacy per-channel language into global config if missing."""
        try:
            config_path = paths.get_config_path()
            if not config_path.exists():
                return
            config_payload = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(config_payload, dict) and "language" in config_payload:
                return

            settings_path = paths.get_settings_path()
            if not settings_path.exists():
                return
            settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
            channels = settings_payload.get("channels") if isinstance(settings_payload, dict) else None
            if not isinstance(channels, dict):
                return

            counts: dict[str, int] = {}
            supported_languages = set(get_supported_languages())
            for payload in channels.values():
                if not isinstance(payload, dict):
                    continue
                value = payload.get("language")
                if value in supported_languages:
                    counts[value] = counts.get(value, 0) + 1

            if not counts:
                return

            chosen = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
            if len(counts) > 1:
                logger.warning(
                    "Multiple per-channel languages found; using '%s' for global config (%s)",
                    chosen,
                    counts,
                )

            from config.v2_config import V2Config

            v2_config = V2Config.load()
            v2_config.language = chosen
            v2_config.save()
            self.config.language = chosen
            logger.info("Migrated legacy per-channel language to global config: %s", chosen)
        except Exception as err:
            logger.warning("Failed to migrate legacy language setting: %s", err)

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

    def _validate_default_backend(self):
        """Validate default_backend against registered agents and fallback if needed."""
        current_default = self.agent_router.global_default
        registered = set(self.agent_service.agents.keys())

        if current_default not in registered:
            # Find a fallback from registered agents
            # Prefer: opencode > claude > codex > any
            for fallback in ["opencode", "claude", "codex"]:
                if fallback in registered:
                    logger.warning(
                        f"Configured default_backend '{current_default}' is not enabled. Falling back to '{fallback}'."
                    )
                    self.agent_router.global_default = fallback
                    for route in self.agent_router.platform_routes.values():
                        route.default = fallback
                    return

            # If no preferred fallback, use any registered agent
            if registered:
                fallback = next(iter(registered))
                logger.warning(
                    f"Configured default_backend '{current_default}' is not enabled. Falling back to '{fallback}'."
                )
                self.agent_router.global_default = fallback
                for route in self.agent_router.platform_routes.values():
                    route.default = fallback
            else:
                logger.error("No agents are registered! Check your configuration.")

    def _setup_callbacks(self):
        """Setup callback connections between modules"""

        # Command handlers dict
        # Admin protection for "set_cwd" and "settings" is now handled by
        # the centralized auth pipeline (core.auth.check_auth) in IM entry points.
        command_handlers = {
            "start": self._dispatch_to_controller_loop(self.command_handler.handle_start),
            "new": self._dispatch_to_controller_loop(self.command_handler.handle_new),
            "cwd": self._dispatch_to_controller_loop(self.command_handler.handle_cwd),
            "set_cwd": self._dispatch_to_controller_loop(self.command_handler.handle_set_cwd),
            "settings": self._dispatch_to_controller_loop(self.settings_handler.handle_settings),
            "stop": self._dispatch_to_controller_loop(self.command_handler.handle_stop),
            "bind": self._dispatch_to_controller_loop(self.command_handler.handle_bind),
        }

        # Register callbacks with the IM client
        self.im_client.register_callbacks(
            on_message=self._dispatch_to_controller_loop(self.message_handler.handle_user_message),
            on_command=command_handlers,
            on_callback_query=self._dispatch_to_controller_loop(self.message_handler.handle_callback_query),
            on_settings_update=self._dispatch_to_controller_loop(self.settings_handler.handle_settings_update),
            on_change_cwd=self._dispatch_to_controller_loop(self.command_handler.handle_change_cwd_submission),
            on_routing_update=self._dispatch_to_controller_loop(self.settings_handler.handle_routing_update),
            on_routing_modal_update=self._dispatch_to_controller_loop(
                self.settings_handler.handle_routing_modal_update
            ),
            on_resume_session=self._dispatch_to_controller_loop(self.session_handler.handle_resume_session_submission),
            on_ready=self._dispatch_to_controller_loop(self._on_im_ready),
        )

    def _dispatch_to_controller_loop(self, callback):
        async def _wrapped(*args, **kwargs):
            loop = self._loop
            if loop is None:
                return await callback(*args, **kwargs)

            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None

            if current_loop is loop:
                return await callback(*args, **kwargs)

            future = asyncio.run_coroutine_threadsafe(callback(*args, **kwargs), loop)
            return await asyncio.wrap_future(future)

        return _wrapped

    def _run_im_runtime(self) -> None:
        try:
            self.im_client.run()
        except BaseException as exc:  # noqa: BLE001
            self._im_run_exception = exc
            logger.error("IM runtime thread exited with error: %s", exc, exc_info=True)
        finally:
            loop = self._loop
            if loop and loop.is_running():
                loop.call_soon_threadsafe(loop.stop)

    async def _on_im_ready(self):
        """Called when IM client is connected and ready.

        Used to restore active poll loops that were interrupted by restart.
        """
        logger.info("IM client ready, checking for active polls to restore...")
        opencode_agent = self.agent_service.agents.get("opencode")
        if opencode_agent and hasattr(opencode_agent, "restore_active_polls"):
            try:
                restored = await opencode_agent.restore_active_polls()  # type: ignore[attr-defined]
                if restored > 0:
                    logger.info(f"Restored {restored} active OpenCode poll(s)")
            except Exception as e:
                logger.error(f"Failed to restore active polls: {e}", exc_info=True)

        # Start update checker and send any pending post-update notification
        try:
            await self.update_checker.check_and_send_post_update_notification()
            self.update_checker.start()
        except Exception as e:
            logger.error(f"Failed to start update checker: {e}", exc_info=True)

    # Utility methods used by handlers

    def get_cwd(self, context: MessageContext) -> str:
        """Get working directory based on context (channel/chat)
        This is the SINGLE source of truth for CWD
        """
        # Get the settings key based on context
        settings_key = self._get_settings_key(context)

        # Get custom CWD from settings
        settings_manager = self.get_settings_manager_for_context(context)
        custom_cwd = settings_manager.get_custom_cwd(settings_key)

        # Use custom CWD if available, otherwise use default from config
        if custom_cwd:
            abs_path = os.path.abspath(os.path.expanduser(custom_cwd))
            if os.path.exists(abs_path):
                return abs_path
            # Try to create it
            try:
                os.makedirs(abs_path, exist_ok=True)
                logger.info(f"Created custom CWD: {abs_path}")
                return abs_path
            except OSError as e:
                logger.warning(f"Failed to create custom CWD '{abs_path}': {e}, using default")

        # Fall back to default from config.json
        default_cwd = self.config.claude.cwd
        if default_cwd:
            return os.path.abspath(os.path.expanduser(default_cwd))

        # Last resort: current directory
        return os.getcwd()

    def _get_settings_key(self, context: MessageContext) -> str:
        """Get settings key based on context.

        For DM contexts, returns user_id so per-user settings apply.
        For channel contexts, returns channel_id for per-channel settings.

        Relies on the ``is_dm`` flag set by the IM layer in
        ``context.platform_specific`` (see Phase 2 of the refactoring).
        """
        is_dm = (context.platform_specific or {}).get("is_dm", False)
        return context.user_id if is_dm else context.channel_id

    def _get_session_key(self, context: MessageContext) -> str:
        """Get a globally unique session-scope key.

        Unlike ``_get_settings_key`` (which returns a raw ID for settings
        lookup routed by platform), this key must be unique across all
        platforms so that sessions, polls, and message-consolidation
        tracking never collide.
        """
        platform = context.platform or (context.platform_specific or {}).get("platform") or self.primary_platform
        return f"{platform}::{self._get_settings_key(context)}"

    def get_im_client_for_context(self, context: Optional[MessageContext] = None) -> BaseIMClient:
        if context is None:
            return self.im_clients[self.primary_platform]
        platform = context.platform or (context.platform_specific or {}).get("platform") or self.primary_platform
        return self.im_clients.get(platform, self.im_clients[self.primary_platform])

    def _get_im_client_for_platform(self, platform: str) -> BaseIMClient:
        return self.im_clients.get(platform, self.im_clients[self.primary_platform])

    def get_settings_manager_for_context(self, context: Optional[MessageContext] = None) -> SettingsManager:
        if context is None:
            return self.platform_settings_managers[self.primary_platform]
        platform = context.platform or (context.platform_specific or {}).get("platform") or self.primary_platform
        return self.platform_settings_managers.get(platform, self.platform_settings_managers[self.primary_platform])

    def update_thread_message_id(self, context: MessageContext) -> None:
        """Update message tracking for consolidated log dispatch."""
        self.message_dispatcher.update_thread_message_id(context)

    async def clear_consolidated_message_id(
        self, context: MessageContext, trigger_message_id: Optional[str] = None
    ) -> None:
        """Clear consolidated message anchor so next log chunk starts fresh."""
        await self.message_dispatcher.clear_consolidated_message_id(context, trigger_message_id)

    def resolve_agent_for_context(self, context: MessageContext) -> str:
        """Unified agent resolution with dynamic override support.

        Priority:
        1. channel_routing.agent_backend (from settings.json)
        2. AgentRouter platform default (configured in code)
        3. AgentService.default_agent ("claude")
        """
        settings_key = self._get_settings_key(context)

        # Check dynamic override first
        settings_manager = self.get_settings_manager_for_context(context)
        routing = settings_manager.get_channel_routing(settings_key)
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
        platform = context.platform or (context.platform_specific or {}).get("platform") or self.primary_platform
        resolved = self.agent_router.resolve(platform, settings_key)

        return resolved

    def get_opencode_overrides(self, context: MessageContext) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Get OpenCode agent, model, and reasoning effort overrides for this channel.

        Returns:
            Tuple of (opencode_agent, opencode_model, opencode_reasoning_effort)
            or (None, None, None) if no overrides.
        """
        settings_key = self._get_settings_key(context)
        settings_manager = self.get_settings_manager_for_context(context)
        routing = settings_manager.get_channel_routing(settings_key)
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
        """Backward-compatible entrypoint; delegated to message dispatcher."""
        await self.message_dispatcher.emit_agent_message(
            context=context,
            message_type=message_type,
            text=text,
            parse_mode=parse_mode,
        )

    # Main run method
    def run(self):
        """Run the controller"""
        logger.info("Starting Claude Proxy Controller with platforms: %s", ", ".join(self.enabled_platforms))

        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._im_thread = threading.Thread(target=self._run_im_runtime, name="im-runtime", daemon=True)
            self._im_thread.start()
            self._loop.run_forever()
            if self._im_run_exception and not isinstance(self._im_run_exception, (KeyboardInterrupt, SystemExit)):
                raise self._im_run_exception
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
        except Exception as e:
            logger.error(f"Error in main run loop: {e}", exc_info=True)
        finally:
            self.cleanup_sync()
            if self._loop is not None:
                try:
                    self._loop.stop()
                except Exception:
                    pass
                self._loop.close()
                self._loop = None

    async def periodic_cleanup(self):
        """[Deprecated] Periodic cleanup is disabled in favor of safe on-demand cleanup"""
        logger.info("periodic_cleanup is deprecated and not scheduled.")
        return

    def cleanup_sync(self):
        """Best-effort synchronous cleanup without cross-loop awaits"""
        logger.info("Cleaning up controller resources (sync, best-effort)...")

        # Stop update checker
        try:
            self.update_checker.stop()
        except Exception as e:
            logger.debug(f"Update checker cleanup skipped: {e}")

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
        except Exception as e:
            logger.warning("Failed to stop IM client: %s", e)

        # Best-effort async shutdown for IM clients
        try:
            shutdown_attr = getattr(self.im_client, "shutdown", None)
            if callable(shutdown_attr):
                import inspect

                if inspect.iscoroutinefunction(shutdown_attr):
                    loop = self._loop
                    if loop and loop.is_running():
                        try:
                            future = asyncio.run_coroutine_threadsafe(shutdown_attr(), loop)
                            future.result(timeout=5)
                        except Exception:
                            pass
                else:
                    shutdown_attr()
        except Exception as e:
            logger.warning("Failed to shutdown IM client: %s", e)

        if self._im_thread and self._im_thread.is_alive():
            self._im_thread.join(timeout=5)
        self._im_thread = None

        # Stop OpenCode server if running
        try:
            from modules.agents.opencode import OpenCodeServerManager

            OpenCodeServerManager.stop_instance_sync()
        except Exception as e:
            logger.debug(f"OpenCode server cleanup skipped: {e}")

        logger.info("Controller cleanup (sync) complete")
