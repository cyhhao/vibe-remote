"""Core controller that coordinates between modules and handlers"""

import asyncio
import os
import logging
from typing import Optional, Dict, Any
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

logger = logging.getLogger(__name__)


class Controller:
    """Main controller that coordinates all bot operations"""

    def __init__(self, config):
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

        # Initialize update checker (use default config if not present)
        from config.v2_config import UpdateConfig
        update_config = getattr(config, 'update', None) or UpdateConfig()
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

        # Agent routing
        self.agent_router = AgentRouter.from_file(None, platform=self.config.platform)

        # Default backend preference:
        # If OpenCode is enabled, make it the implicit default backend.
        if self.config.opencode:
            self.agent_router.global_default = "opencode"
            platform_route = self.agent_router.platform_routes.get(self.config.platform)
            if platform_route:
                platform_route.default = "opencode"

        # Inject settings_manager into SlackBot if it's Slack platform
        if self.config.platform == "slack":
            # Import here to avoid circular dependency
            from modules.im.slack import SlackBot
            if isinstance(self.im_client, SlackBot):
                self.im_client.set_settings_manager(self.settings_manager)
                self.im_client.set_controller(self)
                logger.info("Injected settings_manager and controller into SlackBot")

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
        # Include message_id to distinguish different conversation rounds within same thread
        # Each user message triggers a new round with its own consolidated message
        trigger_id = context.message_id or ""
        return f"{settings_key}:{thread_key}:{trigger_id}"

    def _get_consolidated_message_lock(self, key: str) -> asyncio.Lock:
        if key not in self._consolidated_message_locks:
            self._consolidated_message_locks[key] = asyncio.Lock()
        return self._consolidated_message_locks[key]

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
        # Binary search for the right character position
        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text
        # Truncate bytes and decode, handling partial characters
        truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
        return truncated.rstrip() + "…"
        prefix = "…(truncated)…\n\n"
        keep = max(0, max_chars - len(prefix))
        return f"{prefix}{text[-keep:]}"

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
            if len(text) <= self._get_result_max_chars():
                await self.im_client.send_message(
                    target_context, text, parse_mode=parse_mode
                )
                return

            summary = self._build_result_summary(text, self._get_result_max_chars())
            await self.im_client.send_message(
                target_context, summary, parse_mode=parse_mode
            )

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

                if existing_message_id:
                    try:
                        await self.im_client.edit_message(
                            target_context,
                            existing_message_id,
                            text=first_part,
                            parse_mode="markdown",
                        )
                    except Exception as err:
                        logger.warning(f"Failed to edit oversized Log Message: {err}")
                else:
                    try:
                        await self.im_client.send_message(
                            target_context, first_part, parse_mode="markdown"
                        )
                    except Exception as err:
                        logger.error(f"Failed to send oversized Log Message: {err}")

                # Continue with remainder (skip the part we already sent)
                # Find how many characters fit in target_bytes
                sent_chars = len(first_part) - len(continuation_notice)
                updated = updated[sent_chars:].lstrip()
                existing_message_id = None
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
                new_id = await self.im_client.send_message(
                    target_context, updated, parse_mode="markdown"
                )
                self._consolidated_message_ids[consolidated_key] = new_id
            except Exception as err:
                logger.error(f"Failed to send Log Message: {err}", exc_info=True)

    # Settings update handler (for Slack modal)
    async def handle_settings_update(
        self, user_id: str, show_message_types: list, channel_id: Optional[str] = None,
        require_mention: Optional[bool] = None,
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
            user_settings.show_message_types = show_message_types

            # Save settings - using the correct method name
            self.settings_manager.update_user_settings(settings_key, user_settings)

            # Save require_mention setting
            self.settings_manager.set_require_mention(settings_key, require_mention)

            logger.info(
                f"Updated settings for {settings_key}: show types = {show_message_types}, "
                f"require_mention = {require_mention}"
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
        self, user_id: str, new_cwd: str, channel_id: Optional[str] = None
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
            all_backends = list(self.agent_service.agents.keys())
            registered_backends = sorted(
                all_backends, key=lambda x: (x != "opencode", x)
            )
            current_backend = self.resolve_agent_for_context(context)

            values = view.get("state", {}).get("values", {})
            backend_data = values.get("backend_block", {}).get("backend_select", {})
            selected_backend = backend_data.get("selected_option", {}).get("value")
            if not selected_backend:
                selected_backend = current_backend

            def _selected_value(block_id: str, action_id: str) -> Optional[str]:
                data = values.get(block_id, {}).get(action_id, {})
                return data.get("selected_option", {}).get("value")

            def _selected_prefixed_value(
                block_id: str, action_prefix: str
            ) -> Optional[str]:
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
            oc_reasoning = _selected_prefixed_value(
                "opencode_reasoning_block", "opencode_reasoning_select"
            )

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

            if oc_agent == "__default__":
                oc_agent = None
            if oc_model == "__default__":
                oc_model = None
            if oc_reasoning == "__default__":
                oc_reasoning = None

            opencode_agents = []
            opencode_models = {}
            opencode_default_config = {}

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
        require_mention: Optional[bool] = None,
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

            # Save require_mention setting
            self.settings_manager.set_require_mention(settings_key, require_mention)

            # Build confirmation message
            parts = [f"Backend: **{backend}**"]
            if backend == "opencode":
                if opencode_agent:
                    parts.append(f"Agent: **{opencode_agent}**")
                if opencode_model:
                    parts.append(f"Model: **{opencode_model}**")
                if opencode_reasoning_effort:
                    parts.append(f"Reasoning Effort: **{opencode_reasoning_effort}**")

            # Add require_mention status to confirmation
            if require_mention is None:
                parts.append("Require @mention: **(Default)**")
            elif require_mention:
                parts.append("Require @mention: **Yes**")
            else:
                parts.append("Require @mention: **No**")

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
                f"agent={opencode_agent}, model={opencode_model}, require_mention={require_mention}"
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
