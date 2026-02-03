"""Core controller that coordinates between modules and handlers"""

import asyncio
import json
import os
import logging
from typing import Optional, Dict, Any
from config import paths
from modules.im import BaseIMClient, MessageContext, IMFactory
from modules.im.formatters import SlackFormatter
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
from core.update_checker import UpdateChecker
from vibe.i18n import get_supported_languages, t as i18n_t

logger = logging.getLogger(__name__)


class Controller:
    """Main controller that coordinates all bot operations"""

    def __init__(self, config):
        """Initialize controller with configuration"""
        self.config = config
        self._config_mtime: Optional[float] = None

        # Session tracking (must be initialized before handlers)
        self.claude_sessions: Dict[str, Any] = {}
        self.receiver_tasks: Dict[str, asyncio.Task] = {}
        self.stored_session_mappings: Dict[str, str] = {}

        # Consolidated message tracking (system/assistant/toolcall)
        self._consolidated_message_ids: Dict[str, str] = {}
        self._consolidated_message_buffers: Dict[str, str] = {}
        self._consolidated_message_locks: Dict[str, asyncio.Lock] = {}
        # Track current message_id per thread - ensures log messages follow the latest user message
        # Key: "{settings_key}:{thread_id}", Value: message_id
        self._thread_current_message_id: Dict[str, str] = {}

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
        # Create IM client with platform-specific formatter
        self.im_client: BaseIMClient = IMFactory.create_client(self.config)

        # Create platform-specific formatter
        formatter = SlackFormatter()

        # Inject formatter into clients
        self.im_client.formatter = formatter
        self.claude_client = ClaudeClient(self.config.claude, formatter)

        # Initialize managers
        self.session_manager = SessionManager()
        self.settings_manager = SettingsManager()

        # Migrate legacy per-channel language into global config
        self._migrate_language_from_settings()

        # Agent routing - use configured default_backend
        default_backend = getattr(self.config, "default_backend", "opencode")
        self.agent_router = AgentRouter.from_file(None, platform=self.config.platform, default_backend=default_backend)

        # Inject settings_manager into SlackBot if it's Slack platform
        if self.config.platform == "slack":
            # Import here to avoid circular dependency
            from modules.im.slack import SlackBot

            if isinstance(self.im_client, SlackBot):
                self.im_client.set_settings_manager(self.settings_manager)
                self.im_client.set_controller(self)
                logger.info("Injected settings_manager and controller into SlackBot")

    def _get_lang(self) -> str:
        self._refresh_language_from_config()
        return getattr(self.config, "language", "en")

    def _t(self, key: str, **kwargs) -> str:
        return i18n_t(key, self._get_lang(), **kwargs)

    def _refresh_language_from_config(self) -> None:
        try:
            config_path = paths.get_config_path()
            if not config_path.exists():
                return
            mtime = config_path.stat().st_mtime
            if self._config_mtime != mtime:
                from config.v2_config import V2Config

                v2_config = V2Config.load()
                self.config.language = v2_config.language
                self._config_mtime = mtime
        except Exception as err:
            logger.debug("Failed to reload language from config: %s", err)

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
            on_resume_session=self.handle_resume_session_submission,
            on_ready=self._on_im_ready,
        )

    async def _on_im_ready(self):
        """Called when IM client is connected and ready.

        Used to restore active poll loops that were interrupted by restart.
        """
        logger.info("IM client ready, checking for active polls to restore...")
        opencode_agent = self.agent_service.agents.get("opencode")
        if opencode_agent and hasattr(opencode_agent, "restore_active_polls"):
            try:
                restored = await opencode_agent.restore_active_polls()
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
        custom_cwd = self.settings_manager.get_custom_cwd(settings_key)

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
        """Get settings key based on context"""
        # Slack only in V2
        return context.channel_id

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
        # Use the tracked current message_id for this thread if available
        # This ensures log messages follow the latest user message, even when
        # agent receivers hold stale context references
        tracking_key = f"{settings_key}:{thread_key}"
        trigger_id = self._thread_current_message_id.get(tracking_key) or context.message_id or ""
        return f"{settings_key}:{thread_key}:{trigger_id}"

    def update_thread_message_id(self, context: MessageContext) -> None:
        """Update the current message_id for a thread.

        Call this when processing a new user message to ensure subsequent
        log messages (from agents) are grouped with this message.
        """
        if not context.message_id:
            return
        settings_key = self._get_settings_key(context)
        thread_key = context.thread_id or context.channel_id
        tracking_key = f"{settings_key}:{thread_key}"
        self._thread_current_message_id[tracking_key] = context.message_id

    def _get_consolidated_message_lock(self, key: str) -> asyncio.Lock:
        if key not in self._consolidated_message_locks:
            self._consolidated_message_locks[key] = asyncio.Lock()
        return self._consolidated_message_locks[key]

    async def clear_consolidated_message_id(
        self, context: MessageContext, trigger_message_id: Optional[str] = None
    ) -> None:
        """Clear consolidated message ID so next log message starts fresh.

        Call this after user answers a question to make subsequent log messages
        appear after the user's reply instead of editing the old consolidated message.

        Args:
            context: The message context
            trigger_message_id: If provided, use this instead of context.message_id
                               for the consolidated key (needed when context is from
                               user's answer message, not original request)
        """
        # Build key with the original trigger message_id if provided
        settings_key = self._get_settings_key(context)
        thread_key = context.thread_id or context.channel_id
        msg_id = trigger_message_id if trigger_message_id else (context.message_id or "")
        key = f"{settings_key}:{thread_key}:{msg_id}"

        # Use the same per-key lock as emit_agent_message to avoid race conditions
        lock = self._get_consolidated_message_lock(key)
        async with lock:
            self._consolidated_message_ids.pop(key, None)
            # Also clear the buffer so we don't append to stale content
            self._consolidated_message_buffers.pop(key, None)

    def _get_consolidated_max_bytes(self) -> int:
        # Slack API hard limit is exactly 4000 BYTES (not characters) for chat.update
        # Chinese/emoji characters take 3-4 bytes each in UTF-8
        return 4000

    def _get_consolidated_split_threshold(self) -> int:
        # When accumulated message exceeds this threshold (in bytes), start a new message
        # to avoid Slack edit failures. Use 90% of max to leave some buffer.
        return 3600

    def _get_text_byte_length(self, text: str) -> int:
        """Get UTF-8 byte length of text (Slack counts bytes, not characters)."""
        return len(text.encode("utf-8"))

    def _get_result_max_chars(self) -> int:
        return 30000

    def _build_result_summary(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        prefix = "Result too long; showing a summary.\n\n"
        suffix = "\n\n…(truncated; see result.md for full output)"
        keep = max(0, max_chars - len(prefix) - len(suffix))
        return f"{prefix}{text[:keep]}{suffix}"

    def _truncate_consolidated(self, text: str, max_bytes: int) -> str:
        """Truncate text to fit within max_bytes (UTF-8 encoded)."""
        if self._get_text_byte_length(text) <= max_bytes:
            return text
        # Reserve space for ellipsis (3 bytes for "…")
        ellipsis = "…"
        ellipsis_bytes = len(ellipsis.encode("utf-8"))  # 3 bytes
        target_bytes = max_bytes - ellipsis_bytes
        # Truncate bytes and decode, handling partial characters
        encoded = text.encode("utf-8")
        truncated = encoded[:target_bytes].decode("utf-8", errors="ignore")
        return truncated.rstrip() + ellipsis

    def resolve_agent_for_context(self, context: MessageContext) -> str:
        """Unified agent resolution with dynamic override support.

        Priority:
        1. channel_routing.agent_backend (from settings.json)
        2. AgentRouter platform default (configured in code)
        3. AgentService.default_agent ("claude")
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
        resolved = self.agent_router.resolve(self.config.platform, settings_key)

        return resolved

    def get_opencode_overrides(self, context: MessageContext) -> tuple[Optional[str], Optional[str], Optional[str]]:
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

        Message Types:
        - Log Messages (system/assistant/toolcall): Consolidated into a single
          editable message per conversation round. Can be hidden by user settings.
        - Result Message: Final output, always sent immediately, not hideable.
        - Notify Message: Notifications, always sent immediately.

        Log Messages are accumulated and edited in-place until they exceed the
        Slack byte limit (4000 bytes UTF-8), then a new message is started.
        """
        if not text or not text.strip():
            return

        canonical_type = self.settings_manager._canonicalize_message_type(message_type or "")
        settings_key = self._get_settings_key(context)

        if canonical_type == "notify":
            target_context = self._get_target_context(context)
            await self.im_client.send_message(target_context, text, parse_mode=parse_mode)
            return

        if canonical_type == "result":
            target_context = self._get_target_context(context)
            if len(text) <= self._get_result_max_chars():
                await self.im_client.send_message(target_context, text, parse_mode=parse_mode)
                return

            summary = self._build_result_summary(text, self._get_result_max_chars())
            await self.im_client.send_message(target_context, summary, parse_mode=parse_mode)

            if self.config.platform == "slack" and hasattr(self.im_client, "upload_markdown"):
                try:
                    await self.im_client.upload_markdown(
                        target_context,
                        title="result.md",
                        content=text,
                        filetype="markdown",
                    )
                except Exception as err:
                    logger.warning(f"Failed to upload result attachment: {err}")
                    await self.im_client.send_message(
                        target_context,
                        "无法上传附件（缺少 files:write 权限或上传失败）。需要我改成分条发送吗？",
                        parse_mode=parse_mode,
                    )
            return

        # Log Messages: system/assistant/toolcall - consolidated into editable message
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
            max_bytes = self._get_consolidated_max_bytes()
            split_threshold = self._get_consolidated_split_threshold()
            existing = self._consolidated_message_buffers.get(consolidated_key, "")
            existing_message_id = self._consolidated_message_ids.get(consolidated_key)

            separator = "\n\n---\n\n" if existing else ""
            updated = f"{existing}{separator}{chunk}" if existing else chunk

            target_context = self._get_target_context(context)
            continuation_notice = "\n\n---\n\n_(continued below...)_"
            continuation_bytes = self._get_text_byte_length(continuation_notice)

            # Case 1: Accumulated message exceeds threshold (in bytes), split off old message
            if existing_message_id and self._get_text_byte_length(updated) > split_threshold:
                old_text = existing + continuation_notice
                old_text = self._truncate_consolidated(old_text, max_bytes)

                try:
                    await self.im_client.edit_message(
                        target_context,
                        existing_message_id,
                        text=old_text,
                        parse_mode="markdown",
                    )
                except Exception as err:
                    logger.warning(f"Failed to finalize old Log Message: {err}")

                # Start fresh with just the new chunk
                self._consolidated_message_buffers[consolidated_key] = chunk
                self._consolidated_message_ids.pop(consolidated_key, None)
                updated = chunk
                existing_message_id = None
                logger.info(
                    "Log Message exceeded %d bytes, starting new message",
                    split_threshold,
                )

            # Case 2: Single chunk (including first message) exceeds max_bytes
            # Split into multiple messages to avoid truncation
            while self._get_text_byte_length(updated) > max_bytes:
                # Find split point that fits within threshold (accounting for continuation notice)
                target_bytes = split_threshold - continuation_bytes
                first_part = self._truncate_consolidated(updated, target_bytes)
                first_part = first_part.rstrip("…") + continuation_notice  # Replace truncation marker

                send_ok = False
                if existing_message_id:
                    try:
                        await self.im_client.edit_message(
                            target_context,
                            existing_message_id,
                            text=first_part,
                            parse_mode="markdown",
                        )
                        send_ok = True
                    except Exception as err:
                        logger.warning(f"Failed to edit oversized Log Message: {err}")
                else:
                    try:
                        await self.im_client.send_message(target_context, first_part, parse_mode="markdown")
                        send_ok = True
                    except Exception as err:
                        logger.error(f"Failed to send oversized Log Message: {err}")

                if not send_ok:
                    # Failed to send/edit - stop splitting and truncate the remainder
                    logger.warning("Stopping split loop due to send failure, truncating remainder")
                    break

                # Continue with remainder (skip the part we already sent)
                # Don't lstrip() - preserve intentional indentation in code blocks
                sent_chars = len(first_part) - len(continuation_notice)
                updated = updated[sent_chars:]
                # Clear both local var and stored ID to avoid editing old message on failure
                existing_message_id = None
                self._consolidated_message_ids.pop(consolidated_key, None)
                logger.info(
                    "Log Message chunk exceeded %d bytes, split and continuing",
                    max_bytes,
                )

            updated = self._truncate_consolidated(updated, max_bytes)
            self._consolidated_message_buffers[consolidated_key] = updated

            if existing_message_id:
                try:
                    ok = await self.im_client.edit_message(
                        target_context,
                        existing_message_id,
                        text=updated,
                        parse_mode="markdown",
                    )
                except Exception as err:
                    logger.warning(f"Failed to edit Log Message: {err}")
                    ok = False
                if ok:
                    return
                self._consolidated_message_ids.pop(consolidated_key, None)

            try:
                new_id = await self.im_client.send_message(target_context, updated, parse_mode="markdown")
                self._consolidated_message_ids[consolidated_key] = new_id
            except Exception as err:
                logger.error(f"Failed to send Log Message: {err}", exc_info=True)

    # Settings update handler (for Slack modal)
    async def handle_settings_update(
        self,
        user_id: str,
        show_message_types: list,
        channel_id: Optional[str] = None,
        require_mention: Optional[bool] = None,
        language: Optional[str] = None,
    ):
        """Handle settings update (typically from Slack modal)"""
        try:
            # Determine settings key - for Slack, always use channel_id
            if self.config.platform == "slack":
                settings_key = channel_id if channel_id else user_id  # fallback to user_id if no channel
            else:
                settings_key = channel_id if channel_id else user_id

            # Update settings
            user_settings = self.settings_manager.get_user_settings(settings_key)
            user_settings.show_message_types = show_message_types

            # Save settings - using the correct method name
            self.settings_manager.update_user_settings(settings_key, user_settings)

            # Save require_mention setting
            self.settings_manager.set_require_mention(settings_key, require_mention)

            # Save language setting to global config
            language_saved = True
            if language is not None and language != self.config.language:
                try:
                    from config.v2_config import V2Config

                    v2_config = V2Config.load()
                    v2_config.language = language
                    v2_config.save()
                    self.config.language = language
                except Exception as err:
                    language_saved = False
                    logger.error(f"Failed to persist language setting: {err}")

            logger.info(
                f"Updated settings for {settings_key}: show types = {show_message_types}, "
                f"require_mention = {require_mention}, language = {language}"
            )

            # Create context for sending confirmation (without 'message' field)
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id if channel_id else user_id,
                platform_specific={},
            )

            # Send confirmation
            await self.im_client.send_message(context, f"✅ {self._t('success.settingsUpdated')}")
            if not language_saved:
                await self.im_client.send_message(
                    context,
                    f"⚠️ {self._t('error.languageUpdateFailed')}",
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
                context,
                f"❌ {self._t('error.settingsUpdateFailed', error=str(e))}",
            )
        

    # Working directory change handler (for Slack modal)
    async def handle_change_cwd_submission(self, user_id: str, new_cwd: str, channel_id: Optional[str] = None):
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
                context,
                f"❌ {self._t('error.cwdSetFailed', error=str(e))}",
            )

    async def handle_resume_session_submission(
        self,
        user_id: str,
        channel_id: Optional[str],
        thread_id: Optional[str],
        agent: Optional[str],
        session_id: Optional[str],
        host_message_ts: Optional[str] = None,
    ) -> None:
        """Bind a provided session_id to the current thread for the chosen agent."""
        from modules.settings_manager import ChannelRouting

        try:
            if not agent or not session_id:
                raise ValueError("Agent and session ID are required to resume.")

            if getattr(self, "agent_service", None):
                available_agents = set(self.agent_service.agents.keys())
                if agent not in available_agents:
                    raise ValueError(f"Agent '{agent}' is not enabled.")

            # Decide whether to reuse current thread or start a new one for clarity.
            reuse_thread = True
            if host_message_ts and thread_id and thread_id == host_message_ts:
                # Resume was initiated from the /start menu message; start a fresh thread.
                reuse_thread = False

            # If DM and no thread provided, reuse channel unless we intentionally create new thread later
            target_thread = thread_id if reuse_thread else None

            # Build confirmation context (top-level message when starting fresh)
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id or user_id,
                thread_id=target_thread or None,
                platform_specific={},
            )

            settings_key = self._get_settings_key(context)
            current_routing = self.settings_manager.get_channel_routing(settings_key)
            opencode_agent = current_routing.opencode_agent if current_routing else None
            opencode_model = current_routing.opencode_model if current_routing else None
            opencode_reasoning_effort = current_routing.opencode_reasoning_effort if current_routing else None

            routing = ChannelRouting(
                agent_backend=agent,
                opencode_agent=opencode_agent,
                opencode_model=opencode_model,
                opencode_reasoning_effort=opencode_reasoning_effort,
            )
            self.settings_manager.set_channel_routing(settings_key, routing)

            agent_label = agent.capitalize()
            confirmation = "\n".join(
                [
                    f"✅ {self._t('success.sessionResumed', agent=agent_label, sessionId=session_id)}",
                    self._t("success.sessionResumedTip1"),
                    self._t("success.sessionResumedTip2"),
                ]
            )

            confirmation_ts = await self.im_client.send_message(context, confirmation, parse_mode="markdown")

            # If we created a fresh top-level message, use it as the new thread anchor
            mapped_thread = target_thread or confirmation_ts
            base_session_id = f"slack_{mapped_thread}"

            # Persist mapping
            self.settings_manager.set_agent_session_mapping(settings_key, agent, base_session_id, session_id)
            # Mark thread active
            self.settings_manager.mark_thread_active(user_id, context.channel_id, mapped_thread)
        except Exception as e:
            logger.error(f"Error resuming session: {e}", exc_info=True)
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id or user_id,
                thread_id=thread_id or None,
                platform_specific={},
            )
            await self.im_client.send_message(
                context,
                f"❌ {self._t('error.resumeSubmitFailed', error=str(e))}",
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
            all_backends = list(self.agent_service.agents.keys())
            registered_backends = sorted(all_backends, key=lambda x: (x != "opencode", x))
            current_backend = self.resolve_agent_for_context(context)

            values = view.get("state", {}).get("values", {})
            backend_data = values.get("backend_block", {}).get("backend_select", {})
            selected_backend = backend_data.get("selected_option", {}).get("value")
            if not selected_backend:
                selected_backend = current_backend

            def _selected_value(block_id: str, action_id: str) -> Optional[str]:
                data = values.get(block_id, {}).get(action_id, {})
                return data.get("selected_option", {}).get("value")

            def _selected_prefixed_value(block_id: str, action_prefix: str) -> Optional[str]:
                block = values.get(block_id, {})
                if not isinstance(block, dict):
                    return None
                for action_id, action_data in block.items():
                    if (
                        isinstance(action_id, str)
                        and action_id.startswith(action_prefix)
                        and isinstance(action_data, dict)
                    ):
                        return action_data.get("selected_option", {}).get("value")
                return None

            oc_agent = _selected_value("opencode_agent_block", "opencode_agent_select")
            oc_model = _selected_value("opencode_model_block", "opencode_model_select")
            oc_reasoning = _selected_prefixed_value("opencode_reasoning_block", "opencode_reasoning_select")

            # For block_actions, the latest selection is carried on the `action` payload.
            action_id = action.get("action_id")
            selected_value = None
            selected_option = action.get("selected_option")
            if isinstance(selected_option, dict):
                selected_value = selected_option.get("value")

            if isinstance(action_id, str) and isinstance(selected_value, str):
                if action_id == "opencode_agent_select":
                    oc_agent = selected_value
                elif action_id == "opencode_model_select":
                    oc_model = selected_value
                elif action_id.startswith("opencode_reasoning_select"):
                    oc_reasoning = selected_value

            # Extract Claude/Codex selections from current action or state
            claude_agent = _selected_value("claude_agent_block", "claude_agent_select")
            claude_model = _selected_value("claude_model_block", "claude_model_select")
            codex_model = _selected_value("codex_model_block", "codex_model_select")
            codex_reasoning = _selected_prefixed_value("codex_reasoning_block", "codex_reasoning_select")

            # Handle action payload for Claude/Codex
            if isinstance(action_id, str) and isinstance(selected_value, str):
                if action_id == "claude_agent_select":
                    claude_agent = selected_value
                elif action_id == "claude_model_select":
                    claude_model = selected_value
                elif action_id == "codex_model_select":
                    codex_model = selected_value
                elif action_id.startswith("codex_reasoning_select"):
                    codex_reasoning = selected_value

            if oc_agent == "__default__":
                oc_agent = None
            if oc_model == "__default__":
                oc_model = None
            if oc_reasoning == "__default__":
                oc_reasoning = None
            if claude_agent == "__default__":
                claude_agent = None
            if claude_model == "__default__":
                claude_model = None
            if codex_model == "__default__":
                codex_model = None
            if codex_reasoning == "__default__":
                codex_reasoning = None

            opencode_agents = []
            opencode_models = {}
            opencode_default_config = {}
            claude_agents = []
            claude_models = []
            codex_models = []

            if "opencode" in registered_backends:
                try:
                    opencode_agent = self.agent_service.agents.get("opencode")
                    if opencode_agent and hasattr(opencode_agent, "_get_server"):
                        server = await opencode_agent._get_server()  # type: ignore[attr-defined]
                        await server.ensure_running()
                        cwd = self.get_cwd(context)
                        opencode_agents = await server.get_available_agents(cwd)
                        opencode_models = await server.get_available_models(cwd)
                        opencode_default_config = await server.get_default_config(cwd)
                except Exception as e:
                    logger.warning(f"Failed to fetch OpenCode data: {e}")

            # Fetch Claude data
            if "claude" in registered_backends:
                try:
                    from vibe.api import claude_agents as get_claude_agents, claude_models as get_claude_models

                    cwd = self.get_cwd(context)
                    agents_result = get_claude_agents(cwd)
                    if agents_result.get("ok"):
                        claude_agents = agents_result.get("agents", [])
                    models_result = get_claude_models()
                    if models_result.get("ok"):
                        claude_models = models_result.get("models", [])
                except Exception as e:
                    logger.warning(f"Failed to fetch Claude data: {e}")

            # Fetch Codex data
            if "codex" in registered_backends:
                try:
                    from vibe.api import codex_models as get_codex_models

                    models_result = get_codex_models()
                    if models_result.get("ok"):
                        codex_models = models_result.get("models", [])
                except Exception as e:
                    logger.warning(f"Failed to fetch Codex data: {e}")

            if hasattr(self.im_client, "update_routing_modal"):
                await self.im_client.update_routing_modal(  # type: ignore[attr-defined]
                    view_id=view_id,
                    view_hash=view_hash,
                    channel_id=resolved_channel_id,
                    registered_backends=registered_backends,
                    current_backend=current_backend,
                    current_routing=current_routing,
                    opencode_agents=opencode_agents,
                    opencode_models=opencode_models,
                    opencode_default_config=opencode_default_config,
                    claude_agents=claude_agents,
                    claude_models=claude_models,
                    codex_models=codex_models,
                    selected_backend=selected_backend,
                    selected_opencode_agent=oc_agent,
                    selected_opencode_model=oc_model,
                    selected_opencode_reasoning=oc_reasoning,
                    selected_claude_agent=claude_agent,
                    selected_claude_model=claude_model,
                    selected_codex_model=codex_model,
                    selected_codex_reasoning=codex_reasoning,
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
        claude_agent: Optional[str] = None,
        claude_model: Optional[str] = None,
        codex_model: Optional[str] = None,
        codex_reasoning_effort: Optional[str] = None,
    ):
        """Handle routing update submission (from Slack modal)"""
        from modules.settings_manager import ChannelRouting

        try:
            # Get settings key
            settings_key = channel_id if channel_id else user_id

            # Get existing routing to preserve settings for other backends
            existing_routing = self.settings_manager.get_channel_routing(settings_key)

            # Merge with existing routing - only update fields for the selected backend
            routing = ChannelRouting(
                agent_backend=backend,
                # OpenCode settings: update if opencode is selected, otherwise preserve existing
                opencode_agent=opencode_agent
                if backend == "opencode"
                else (existing_routing.opencode_agent if existing_routing else None),
                opencode_model=opencode_model
                if backend == "opencode"
                else (existing_routing.opencode_model if existing_routing else None),
                opencode_reasoning_effort=opencode_reasoning_effort
                if backend == "opencode"
                else (existing_routing.opencode_reasoning_effort if existing_routing else None),
                # Claude settings: update if claude is selected, otherwise preserve existing
                claude_agent=claude_agent
                if backend == "claude"
                else (existing_routing.claude_agent if existing_routing else None),
                claude_model=claude_model
                if backend == "claude"
                else (existing_routing.claude_model if existing_routing else None),
                # Codex settings: update if codex is selected, otherwise preserve existing
                codex_model=codex_model
                if backend == "codex"
                else (existing_routing.codex_model if existing_routing else None),
                codex_reasoning_effort=codex_reasoning_effort
                if backend == "codex"
                else (existing_routing.codex_reasoning_effort if existing_routing else None),
            )

            # Save routing
            self.settings_manager.set_channel_routing(settings_key, routing)

            # Build confirmation message
            parts = [f"{self._t('routing.label.backend')}: **{backend}**"]
            if backend == "opencode":
                if opencode_agent:
                    parts.append(f"{self._t('routing.label.agent')}: **{opencode_agent}**")
                if opencode_model:
                    parts.append(f"{self._t('routing.label.model')}: **{opencode_model}**")
                if opencode_reasoning_effort:
                    parts.append(
                        f"{self._t('routing.label.reasoningEffort')}: **{opencode_reasoning_effort}**"
                    )
            elif backend == "claude":
                if claude_agent:
                    parts.append(f"{self._t('routing.label.agent')}: **{claude_agent}**")
                if claude_model:
                    parts.append(f"{self._t('routing.label.model')}: **{claude_model}**")
            elif backend == "codex":
                if codex_model:
                    parts.append(f"{self._t('routing.label.model')}: **{codex_model}**")
                if codex_reasoning_effort:
                    parts.append(
                        f"{self._t('routing.label.reasoningEffort')}: **{codex_reasoning_effort}**"
                    )

            # Create context for confirmation message
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id if channel_id else user_id,
                platform_specific={},
            )

            await self.im_client.send_message(
                context,
                f"✅ {self._t('success.routingUpdated')}\n" + "\n".join(parts),
                parse_mode="markdown",
            )

            logger.info(
                f"Routing updated for {settings_key}: backend={backend}, "
                f"opencode_agent={opencode_agent}, opencode_model={opencode_model}, "
                f"claude_agent={claude_agent}, claude_model={claude_model}, "
                f"codex_model={codex_model}"
            )

        except Exception as e:
            logger.error(f"Error updating routing: {e}")
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id if channel_id else user_id,
                platform_specific={},
            )
            await self.im_client.send_message(
                context,
                f"❌ {self._t('error.routingUpdateFailed', error=str(e))}",
            )

    # Main run method
    def run(self):
        """Run the controller"""
        logger.info(f"Starting Claude Proxy Controller with {self.config.platform} platform...")

        # 不再创建额外事件循环，避免与 IM 客户端的内部事件循环冲突
        # 清理职责改为：
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
                    try:
                        asyncio.run(shutdown_attr())
                    except RuntimeError:
                        pass
                else:
                    shutdown_attr()
        except Exception as e:
            logger.warning("Failed to shutdown IM client: %s", e)

        # Stop OpenCode server if running
        try:
            from modules.agents.opencode import OpenCodeServerManager

            OpenCodeServerManager.stop_instance_sync()
        except Exception as e:
            logger.debug(f"OpenCode server cleanup skipped: {e}")

        logger.info("Controller cleanup (sync) complete")
