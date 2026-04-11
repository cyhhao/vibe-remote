"""Session management handlers for Claude SDK sessions"""

import logging
import os
import time
from typing import Optional, Dict, Any, Tuple
from uuid import uuid4
from modules.im import MessageContext
from modules.claude_sdk_compat import ClaudeSDKClient, ClaudeAgentOptions
from modules.agents.native_sessions.base import build_resume_preview

from .base import BaseHandler

logger = logging.getLogger(__name__)


class SessionHandler(BaseHandler):
    """Handles all session-related operations"""

    def __init__(self, controller):
        """Initialize with reference to main controller"""
        super().__init__(controller)
        self.session_manager = controller.session_manager
        self.claude_sessions = controller.claude_sessions
        self.receiver_tasks = controller.receiver_tasks
        self.stored_session_mappings = controller.stored_session_mappings
        self.session_last_activity = getattr(controller, "session_last_activity", {})
        self.active_sessions = getattr(controller, "claude_active_sessions", set())
        controller.session_last_activity = self.session_last_activity
        controller.claude_active_sessions = self.active_sessions

    def touch_session_activity(self, composite_key: str) -> None:
        if composite_key:
            self.session_last_activity[composite_key] = time.monotonic()

    def mark_session_active(self, composite_key: str) -> None:
        if not composite_key:
            return
        self.active_sessions.add(composite_key)
        self.touch_session_activity(composite_key)

    def mark_session_idle(self, composite_key: str) -> None:
        if not composite_key:
            return
        self.active_sessions.discard(composite_key)
        if composite_key in self.claude_sessions:
            self.touch_session_activity(composite_key)

    def clear_session_tracking(self, composite_key: str) -> None:
        if not composite_key:
            return
        self.active_sessions.discard(composite_key)
        self.session_last_activity.pop(composite_key, None)

    def get_base_session_id(self, context: MessageContext, source: str = "human") -> str:
        """Get base session ID based on platform and context (without path)"""
        platform = self._get_context_platform(context)
        is_dm = bool((context.platform_specific or {}).get("is_dm", False))
        if self.should_allocate_scheduled_anchor(context, source=source):
            return f"{platform}_scheduled-{uuid4().hex}"
        if is_dm:
            use_dm_threads = self._supports_threaded_session(context, is_dm=True)

            if use_dm_threads:
                base_id = context.thread_id or context.message_id or context.channel_id or context.user_id
            else:
                base_id = context.channel_id or context.user_id
        else:
            base_id = context.thread_id
            if not base_id:
                use_message_id = True
                getter = getattr(self.controller, "get_im_client_for_context", None)
                if callable(getter):
                    try:
                        im_client = getter(context)
                    except AttributeError:
                        im_client = getattr(self.controller, "im_client", None)
                else:
                    im_client = getattr(self.controller, "im_client", None)
                if im_client and hasattr(im_client, "should_use_message_id_for_channel_session"):
                    use_message_id = bool(im_client.should_use_message_id_for_channel_session(context))
                base_id = context.message_id if use_message_id and context.message_id else context.channel_id
        return f"{platform}_{base_id}"

    def _get_context_platform(self, context: MessageContext) -> str:
        return (
            context.platform
            or (context.platform_specific or {}).get("platform")
            or getattr(self.config, "platform", "slack")
        )

    def should_allocate_scheduled_anchor(self, context: MessageContext, source: str = "human") -> bool:
        if source != "scheduled" or context.thread_id:
            return False
        is_dm = bool((context.platform_specific or {}).get("is_dm", False))
        if not self._supports_threaded_session(context, is_dm=is_dm):
            return False
        if is_dm:
            return True

        im_client = self._get_im_client(context)
        use_message_id = getattr(im_client, "should_use_message_id_for_channel_session", lambda _context=None: True)
        return bool(use_message_id(context))

    def build_message_anchor_base(self, context: MessageContext, message_id: str) -> str:
        return f"{self._get_context_platform(context)}_{message_id}"

    def alias_session_base(
        self,
        context: MessageContext,
        *,
        source_base_session_id: str,
        alias_base_session_id: str,
        target_session_key: Optional[str] = None,
        source_session_key: Optional[str] = None,
        clear_source: bool = False,
    ) -> bool:
        if not source_base_session_id or not alias_base_session_id:
            return False
        resolved_source_key = source_session_key or self._get_session_key(context)
        resolved_target_key = target_session_key or resolved_source_key
        if resolved_target_key == resolved_source_key:
            changed = self.sessions.alias_session_base(
                resolved_target_key,
                source_base_session_id,
                alias_base_session_id,
            )
        else:
            changed = self.sessions.alias_session_base_across_scopes(
                resolved_source_key,
                resolved_target_key,
                source_base_session_id,
                alias_base_session_id,
            )
        cleared = 0
        if clear_source and source_base_session_id != alias_base_session_id:
            cleared = self.sessions.clear_session_base(resolved_source_key, source_base_session_id)
        return bool(changed or cleared)

    def finalize_scheduled_delivery(self, context: MessageContext, sent_message_id: Optional[str]) -> None:
        payload = context.platform_specific or {}
        if payload.get("turn_source") != "scheduled":
            return
        source_base_session_id = payload.get("turn_base_session_id") or ""
        strategy = payload.get("scheduled_delivery_alias") or {}
        mode = strategy.get("mode") or "none"
        if not source_base_session_id or mode == "none":
            return

        alias_base_session_id: Optional[str] = None
        if mode == "sent_message":
            if not sent_message_id:
                return
            alias_base_session_id = self.build_message_anchor_base(context, sent_message_id)
        elif mode == "fixed_base":
            alias_base_session_id = strategy.get("base_session_id")
        if not alias_base_session_id:
            return

        target_session_key = strategy.get("session_key") or self._get_session_key(context)
        clear_source = bool(strategy.get("clear_source", False))
        self.alias_session_base(
            context,
            source_base_session_id=source_base_session_id,
            alias_base_session_id=alias_base_session_id,
            target_session_key=target_session_key,
            clear_source=clear_source,
        )

        if mode == "sent_message" and sent_message_id:
            platform = self._get_context_platform(context)
            if platform in {"slack", "lark"}:
                delivery_channel_id = payload.get("delivery_override", {}).get("channel_id") or context.channel_id
                self.sessions.mark_thread_active("scheduled", delivery_channel_id, sent_message_id)

    def _supports_threaded_session(self, context: MessageContext, *, is_dm: bool) -> bool:
        getter = getattr(self.controller, "get_im_client_for_context", None)
        if callable(getter):
            try:
                im_client = getter(context)
            except AttributeError:
                im_client = getattr(self.controller, "im_client", None)
        else:
            im_client = getattr(self.controller, "im_client", None)

        if im_client is None:
            return False
        if is_dm:
            return bool(getattr(im_client, "should_use_thread_for_dm_session", lambda: False)())
        return bool(getattr(im_client, "should_use_thread_for_reply", lambda: False)())

    def get_working_path(self, context: MessageContext) -> str:
        """Get working directory - delegate to controller's get_cwd"""
        return self.controller.get_cwd(context)

    def _running_as_root(self) -> bool:
        geteuid = getattr(os, "geteuid", None)
        return bool(geteuid and geteuid() == 0)

    def _should_force_claude_sandbox(self) -> bool:
        if os.environ.get("IS_SANDBOX"):
            return False
        permission_mode = getattr(getattr(self.config, "claude", None), "permission_mode", None)
        return permission_mode == "bypassPermissions" and self._running_as_root()

    def _get_claude_cli_path_override(self) -> Optional[str]:
        cli_path = getattr(getattr(self.config, "claude", None), "cli_path", None)
        if cli_path is None:
            return None

        normalized = str(cli_path).strip()
        if not normalized:
            return None

        if normalized == "claude":
            return None

        return os.path.expanduser(normalized)

    def _load_agent_file(self, agent_name: str, working_path: str) -> Optional[Dict[str, Any]]:
        """Load an agent file and return its parsed content.

        Searches for agent file in:
        1. Project agents: <working_path>/.claude/agents/<agent_name>.md
        2. Global agents: ~/.claude/agents/<agent_name>.md

        Returns:
            Dict with keys: name, description, prompt, tools, model
            or None if not found/parse error.
        """
        from pathlib import Path
        from vibe.api import parse_claude_agent_file

        # Search paths (project first, then global)
        search_paths = [
            Path(working_path) / ".claude" / "agents" / f"{agent_name}.md",
            Path.home() / ".claude" / "agents" / f"{agent_name}.md",
        ]

        for agent_path in search_paths:
            if agent_path.exists() and agent_path.is_file():
                parsed = parse_claude_agent_file(str(agent_path))
                if parsed:
                    return parsed
                else:
                    logger.warning(f"Failed to parse agent file: {agent_path}")

        logger.warning(f"Agent file not found for '{agent_name}' in {search_paths}")
        return None

    def get_session_info(self, context: MessageContext, source: str = "human") -> Tuple[str, str, str]:
        """Get session info: base_session_id, working_path, and composite_key"""
        base_session_id = self.get_base_session_id(context, source=source)
        working_path = self.get_working_path(context)  # Pass context to get user's custom_cwd
        # Create composite key for internal storage
        composite_key = f"{base_session_id}:{working_path}"
        return base_session_id, working_path, composite_key

    async def _prepare_resume_context(
        self,
        context: MessageContext,
        host_message_ts: Optional[str],
        is_dm: bool,
    ) -> MessageContext:
        im_client = self._get_im_client(context)
        prepare = getattr(im_client, "prepare_resume_context", None)
        if not callable(prepare):
            return context
        try:
            prepared = await prepare(context, host_message_ts=host_message_ts, is_dm=is_dm)
        except Exception as exc:
            logger.warning("Failed to prepare resume context for %s: %s", context.platform, exc)
            return context
        return prepared if isinstance(prepared, MessageContext) else context

    def _supports_resume_threading(self, context: MessageContext, *, is_dm: bool) -> bool:
        im_client = self._get_im_client(context)
        if is_dm:
            return bool(getattr(im_client, "should_use_thread_for_dm_session", lambda: False)())
        uses_thread_replies = bool(getattr(im_client, "should_use_thread_for_reply", lambda: False)())
        if not uses_thread_replies:
            return False
        if context.thread_id:
            return True
        uses_message_anchor = bool(
            getattr(im_client, "should_use_message_id_for_channel_session", lambda _context=None: True)(context)
        )
        return uses_message_anchor

    def _build_resume_confirmation(
        self,
        *,
        agent_label: str,
        session_id: str,
        preview: str = "",
    ) -> str:
        lines = [f"✅ {self._t('success.sessionResumed', agent=agent_label, sessionId=session_id)}"]
        if preview:
            lines.extend(["", preview])
        return "\n".join(lines)

    def _build_resume_followup(
        self,
        context: MessageContext,
        *,
        is_dm: bool,
    ) -> str:
        lines: list[str] = []
        platform = context.platform or self.config.platform
        if context.thread_id:
            if platform == "discord":
                lines.append(self._t("success.sessionResumedContinueDiscordThread"))
            elif platform == "lark":
                lines.append(self._t("success.sessionResumedContinueFeishuThread"))
            else:
                lines.append(self._t("success.sessionResumedContinueThread"))
            if not is_dm:
                lines.append(self._t("success.sessionResumedThreadFreshTip"))
        else:
            lines.append(self._t("success.sessionResumedContinueDirect"))
        return "\n".join(line for line in lines if line)

    def _get_resume_preview(
        self,
        context: MessageContext,
        *,
        agent: str,
        session_id: str,
    ) -> str:
        service_getter = getattr(self.controller, "get_native_session_service", None)
        if callable(service_getter):
            native_session_service = service_getter()
        else:
            native_session_service = getattr(self.controller, "native_session_service", None)
        if native_session_service is None:
            return ""
        try:
            working_path = self.get_working_path(context)
            item = native_session_service.get_session(working_path, agent, session_id)
        except Exception as exc:
            logger.warning("Failed to resolve resume preview for %s session %s: %s", agent, session_id, exc)
            return ""
        if item is None:
            return ""
        return build_resume_preview(item.last_agent_message or item.last_agent_tail)

    async def get_or_create_claude_session(
        self,
        context: MessageContext,
        subagent_name: Optional[str] = None,
        subagent_model: Optional[str] = None,
        subagent_reasoning_effort: Optional[str] = None,
    ) -> ClaudeSDKClient:
        """Get existing Claude session or create a new one"""
        base_session_id, working_path, composite_key = self.get_session_info(context)

        settings_key = self._get_settings_key(context)
        session_key = self._get_session_key(context)
        stored_claude_session_id = self.sessions.get_claude_session_id(session_key, base_session_id)

        # Read routing overrides via get_channel_routing which correctly
        # resolves DM users from the users store (not the stale channels store).
        routing = self._get_settings_manager(context).get_channel_routing(settings_key)

        # Priority: subagent params > channel config > agent frontmatter > global default
        # Note: agent frontmatter model is applied later after loading agent file
        effective_agent = subagent_name or (routing.claude_agent if routing else None)
        # Store explicit model override (not including default yet)
        explicit_model = subagent_model or (routing.claude_model if routing else None)
        explicit_effort = subagent_reasoning_effort or (routing.claude_reasoning_effort if routing else None)

        if composite_key in self.claude_sessions and not effective_agent:
            client = self.claude_sessions[composite_key]
            # Apply current model from routing on every request, consistent with
            # how OpenCode/Codex pass the model on each prompt/turn call.
            current_model = explicit_model or self.config.claude.default_model
            try:
                await client.set_model(current_model)
            except Exception as e:
                logger.warning(f"Failed to update model on cached Claude session: {e}")
            logger.info(
                f"Using existing Claude SDK client for {base_session_id} at {working_path} (model={current_model})"
            )
            self.touch_session_activity(composite_key)
            return client

        if effective_agent:
            cached_base = f"{base_session_id}:{effective_agent}"
            cached_key = f"{cached_base}:{working_path}"
            cached_session_id = self.sessions.get_agent_session_id(
                session_key,
                cached_base,
                agent_name="claude",
            )
            if cached_key in self.claude_sessions:
                client = self.claude_sessions[cached_key]
                # Apply explicit model override from routing/subagent params,
                # consistent with how OpenCode/Codex pass the model per request.
                # When no explicit override, keep the agent frontmatter model
                # that was set at session creation.
                if explicit_model:
                    try:
                        await client.set_model(explicit_model)
                    except Exception as e:
                        logger.warning(f"Failed to update model on cached Claude subagent session: {e}")
                logger.info(
                    "Using Claude subagent session for %s at %s (model_override=%s)",
                    cached_base,
                    working_path,
                    explicit_model,
                )
                self.touch_session_activity(cached_key)
                return client
            # Always use agent-specific key when effective_agent is set
            # This ensures session continuity even on first use
            composite_key = cached_key
            base_session_id = cached_base
            if cached_session_id:
                stored_claude_session_id = cached_session_id

        # Ensure working directory exists
        if not os.path.exists(working_path):
            try:
                os.makedirs(working_path, exist_ok=True)
                logger.info(f"Created working directory: {working_path}")
            except Exception as e:
                logger.error(f"Failed to create working directory {working_path}: {e}")
                working_path = os.getcwd()

        # Build system prompt from agent file if subagent is specified
        # Claude Code has a bug where ~/.claude/agents/*.md files are not auto-discovered
        # See: https://github.com/anthropics/claude-code/issues/11205
        # Workaround: read the agent file and use its content as system_prompt
        agent_system_prompt: Optional[str] = None
        agent_allowed_tools: Optional[list] = None
        agent_model: Optional[str] = None
        if effective_agent:
            agent_data = self._load_agent_file(effective_agent, working_path)
            if agent_data:
                agent_system_prompt = agent_data.get("prompt")
                agent_allowed_tools = agent_data.get("tools")
                agent_model = agent_data.get("model")
                logger.info(f"Loaded agent '{effective_agent}' system prompt ({len(agent_system_prompt or '')} chars)")
                if agent_allowed_tools:
                    logger.info(f"  Agent allowed tools: {agent_allowed_tools}")
                if agent_model:
                    logger.info(f"  Agent model from frontmatter: {agent_model}")
            else:
                logger.warning(f"Could not load agent file for '{effective_agent}'")

        # Filter out special values that aren't actual model names
        if agent_model and agent_model.lower() in ("inherit", ""):
            agent_model = None

        # Determine final model: explicit override > agent frontmatter > global default
        effective_model = explicit_model or agent_model or self.config.claude.default_model
        from modules.agents.opencode.utils import normalize_claude_reasoning_effort

        effective_effort = normalize_claude_reasoning_effort(effective_model, explicit_effort)

        # Determine final system prompt: agent prompt takes precedence over config.
        # When reply_enhancements is enabled and no explicit prompt is set,
        # use the claude_code preset with our enhancements appended so the
        # built-in tools/instructions remain intact.
        base_prompt = agent_system_prompt or self.config.claude.system_prompt
        reply_enhancements_on = getattr(self.config, "reply_enhancements", True)

        if reply_enhancements_on:
            from core.reply_enhancer import build_reply_enhancements_prompt

            platform = context.platform or (context.platform_specific or {}).get("platform") or self.config.platform

            reply_prompt = build_reply_enhancements_prompt(
                include_quick_replies=platform != "wechat",
                context=context,
                fallback_platform=platform,
            )

            if base_prompt:
                final_system_prompt = f"{base_prompt}\n\n{reply_prompt}"
            else:
                final_system_prompt = {
                    "type": "preset",
                    "preset": "claude_code",
                    "append": reply_prompt,
                }
        else:
            final_system_prompt = base_prompt

        # Create extra_args for CLI passthrough (fallback for model)
        extra_args: Dict[str, str | None] = {}
        if effective_model:
            extra_args["model"] = effective_model

        # Collect Anthropic-related environment variables to pass to Claude
        claude_env = {}
        for key in os.environ:
            if key.startswith("ANTHROPIC_") or key.startswith("CLAUDE_"):
                claude_env[key] = os.environ[key]
        if self._should_force_claude_sandbox():
            claude_env["IS_SANDBOX"] = "1"
            logger.info("Detected Claude bypassPermissions running as root; forcing IS_SANDBOX=1 for Claude subprocess")

        option_kwargs: Dict[str, Any] = {
            "permission_mode": self.config.claude.permission_mode,
            "cwd": working_path,
            "system_prompt": final_system_prompt,
            "resume": stored_claude_session_id if stored_claude_session_id else None,
            "extra_args": extra_args,
            "setting_sources": ["user"],  # Load user settings from ~/.claude/settings.json
            # Disable AskUserQuestion tool - SDK cannot respond to it programmatically
            # See: https://github.com/anthropics/claude-code/issues/10168
            "disallowed_tools": ["AskUserQuestion"],
            "env": claude_env,  # Pass Anthropic/Claude env vars
        }
        cli_path_override = self._get_claude_cli_path_override()
        if cli_path_override:
            option_kwargs["cli_path"] = cli_path_override
        if effective_effort:
            option_kwargs["effort"] = effective_effort
        # Only set allowed_tools if agent file specifies tools.
        # Omitting the field keeps SDK default tool behavior.
        if agent_allowed_tools:
            option_kwargs["allowed_tools"] = agent_allowed_tools

        options = ClaudeAgentOptions(**option_kwargs)

        # Log session creation details
        logger.info(f"Creating Claude client for {base_session_id} at {working_path}")
        logger.info(f"  Working directory: {working_path}")
        logger.info(f"  Resume session ID: {stored_claude_session_id}")
        logger.info(f"  Options.resume: {options.resume}")
        if effective_agent:
            logger.info(f"  Subagent: {effective_agent}")
        if effective_model:
            logger.info(f"  Model: {effective_model}")
        if effective_effort:
            logger.info(f"  Effort: {effective_effort}")

        # Log if we're resuming a session
        if stored_claude_session_id:
            logger.info(f"Attempting to resume Claude session {stored_claude_session_id}")
        else:
            logger.info(f"Creating new Claude session")

        # Create new Claude client
        client = ClaudeSDKClient(options=options)

        # Log the actual options being used
        logger.info("ClaudeAgentOptions details:")
        logger.info(f"  - permission_mode: {options.permission_mode}")
        logger.info(f"  - cwd: {options.cwd}")
        logger.info(f"  - system_prompt: {options.system_prompt}")
        logger.info(f"  - resume: {options.resume}")
        logger.info(f"  - continue_conversation: {options.continue_conversation}")
        logger.info(f"  - cli_path: {options.cli_path}")
        if subagent_name:
            logger.info(f"  - subagent: {subagent_name}")

        # Connect the client
        await client.connect()

        self.claude_sessions[composite_key] = client
        self.touch_session_activity(composite_key)
        logger.info(f"Created new Claude SDK client for {base_session_id} at {working_path}")

        return client

    async def handle_resume_session_submission(
        self,
        user_id: str,
        channel_id: Optional[str],
        thread_id: Optional[str],
        agent: Optional[str],
        session_id: Optional[str],
        host_message_ts: Optional[str] = None,
        is_dm: bool = False,
        platform: Optional[str] = None,
    ) -> None:
        """Bind a provided session_id to the current thread for the chosen agent."""
        from modules.settings_manager import ChannelRouting

        try:
            if not agent or not session_id:
                raise ValueError("Agent and session ID are required to resume.")

            if getattr(self.controller, "agent_service", None):
                available_agents = set(self.controller.agent_service.agents.keys())
                if agent not in available_agents:
                    raise ValueError(f"Agent '{agent}' is not enabled.")

            reuse_thread = True
            if host_message_ts and thread_id and thread_id == host_message_ts:
                reuse_thread = False

            target_thread = thread_id if reuse_thread else None

            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id or user_id,
                platform=platform or self.config.platform,
                thread_id=target_thread or None,
                message_id=host_message_ts or None,
                platform_specific={"is_dm": is_dm},
            )
            thread_capable = self._supports_resume_threading(context, is_dm=is_dm)

            settings_key = self._get_settings_key(context)
            session_key = self._get_session_key(context)
            settings_manager = self._get_settings_manager(context)
            current_routing = settings_manager.get_channel_routing(settings_key)

            routing = ChannelRouting(
                agent_backend=agent,
                opencode_agent=current_routing.opencode_agent if current_routing else None,
                opencode_model=current_routing.opencode_model if current_routing else None,
                opencode_reasoning_effort=current_routing.opencode_reasoning_effort if current_routing else None,
                claude_agent=current_routing.claude_agent if current_routing else None,
                claude_model=current_routing.claude_model if current_routing else None,
                claude_reasoning_effort=current_routing.claude_reasoning_effort if current_routing else None,
                codex_model=current_routing.codex_model if current_routing else None,
                codex_reasoning_effort=current_routing.codex_reasoning_effort if current_routing else None,
            )
            settings_manager.set_channel_routing(settings_key, routing)

            agent_label = agent.capitalize()
            preview = self._get_resume_preview(context, agent=agent, session_id=session_id)
            confirmation = self._build_resume_confirmation(
                agent_label=agent_label,
                session_id=session_id,
                preview=preview,
            )

            initial_context = context
            if thread_capable and not target_thread:
                initial_context = MessageContext(
                    user_id=context.user_id,
                    channel_id=context.channel_id,
                    platform=context.platform,
                    thread_id=None,
                    message_id=context.message_id,
                    platform_specific=context.platform_specific,
                    files=context.files,
                )

            confirmation_ts = await self._get_im_client(initial_context).send_message(
                initial_context, confirmation, parse_mode="markdown"
            )

            followup_context = context
            if thread_capable and not target_thread:
                anchor_context = MessageContext(
                    user_id=context.user_id,
                    channel_id=context.channel_id,
                    platform=context.platform,
                    thread_id=None,
                    message_id=confirmation_ts,
                    platform_specific=context.platform_specific,
                    files=context.files,
                )
                followup_context = await self._prepare_resume_context(anchor_context, confirmation_ts, is_dm)

            followup = self._build_resume_followup(followup_context, is_dm=is_dm)
            if followup:
                await self._get_im_client(followup_context).send_message(
                    followup_context,
                    followup,
                    parse_mode="markdown",
                )

            mapped_thread = followup_context.thread_id or confirmation_ts
            if thread_capable:
                mapping_context = MessageContext(
                    user_id=user_id,
                    channel_id=followup_context.channel_id,
                    platform=followup_context.platform,
                    thread_id=mapped_thread,
                    message_id=confirmation_ts,
                    platform_specific={"is_dm": is_dm},
                )
            else:
                mapping_context = MessageContext(
                    user_id=user_id,
                    channel_id=followup_context.channel_id,
                    platform=followup_context.platform,
                    thread_id=None,
                    message_id=None,
                    platform_specific={"is_dm": is_dm},
                )
            base_session_id = self.get_base_session_id(mapping_context)

            # OpenCode session mappings use composite keys that include
            # working_path so that cwd changes create new sessions.
            mapping_key = base_session_id
            if agent == "opencode":
                working_path = self.get_working_path(mapping_context)
                mapping_key = f"{base_session_id}:{working_path}"

            self.sessions.set_agent_session_mapping(session_key, agent, mapping_key, session_id)
            self.sessions.mark_thread_active(user_id, context.channel_id, mapped_thread)
        except Exception as e:
            logger.error(f"Error resuming session: {e}", exc_info=True)
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id or user_id,
                platform=platform or self.config.platform,
                thread_id=thread_id or None,
                platform_specific={"is_dm": is_dm},
            )
            await self._get_im_client(context).send_message(
                context,
                f"❌ {self._t('error.resumeSubmitFailed', error=str(e))}",
            )

    async def cleanup_session(self, composite_key: str):
        """Clean up a specific session by composite key"""
        # Cancel receiver task if exists
        if composite_key in self.receiver_tasks:
            task = self.receiver_tasks[composite_key]
            if not task.done():
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
            del self.receiver_tasks[composite_key]
            logger.info(f"Cancelled receiver task for session {composite_key}")

        # Cleanup Claude session
        if composite_key in self.claude_sessions:
            client = self.claude_sessions[composite_key]
            try:
                await client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting Claude session {composite_key}: {e}")
            del self.claude_sessions[composite_key]
            logger.info(f"Cleaned up Claude session {composite_key}")

        self.clear_session_tracking(composite_key)

    async def evict_idle_sessions(self, idle_timeout: float) -> int:
        """Disconnect Claude sessions that have been idle beyond the timeout."""
        if idle_timeout <= 0:
            return 0

        now = time.monotonic()
        expired: list[tuple[str, float]] = []

        for composite_key, last_activity in list(self.session_last_activity.items()):
            if composite_key not in self.claude_sessions:
                self.session_last_activity.pop(composite_key, None)
                self.active_sessions.discard(composite_key)
                continue
            if composite_key in self.active_sessions:
                continue
            if now - last_activity >= idle_timeout:
                expired.append((composite_key, now - last_activity))

        for composite_key, idle_for in expired:
            logger.info("Evicting idle Claude session %s after %.1fs idle", composite_key, idle_for)
            await self.cleanup_session(composite_key)

        return len(expired)

    async def handle_session_error(self, composite_key: str, context: MessageContext, error: Exception):
        """Handle session-related errors"""
        error_msg = str(error)

        # Check for specific error types
        if "read() called while another coroutine" in error_msg:
            logger.error(f"Session {composite_key} has concurrent read error - cleaning up")
            await self.cleanup_session(composite_key)

            # Notify user and suggest retry
            await self._get_im_client(context).send_message(
                context,
                self._get_formatter(context).format_error(self._t("error.sessionReset")),
            )
        elif "Session is broken" in error_msg or "Connection closed" in error_msg or "Connection lost" in error_msg:
            logger.error(f"Session {composite_key} is broken - cleaning up")
            await self.cleanup_session(composite_key)

            # Notify user
            await self._get_im_client(context).send_message(
                context,
                self._get_formatter(context).format_error(self._t("error.sessionConnectionLost")),
            )
        else:
            # Generic error handling
            logger.error(f"Error in session {composite_key}: {error}")
            await self._get_im_client(context).send_message(
                context,
                self._get_formatter(context).format_error(self._t("error.sessionGeneric", error=error_msg)),
            )

    def capture_session_id(self, base_session_id: str, claude_session_id: str, session_key: str):
        """Capture and store Claude session ID mapping"""
        self.sessions.set_session_mapping(session_key, base_session_id, claude_session_id)

        logger.info(f"Captured Claude session_id: {claude_session_id} for {base_session_id}")

    def restore_session_mappings(self):
        """Restore session mappings from settings on startup"""
        logger.info("Initializing session mappings from saved settings...")

        session_state = self.sessions.get_all_session_mappings()

        restored_count = 0
        for user_id, agent_map in session_state.items():
            claude_map = agent_map.get("claude", {}) if isinstance(agent_map, dict) else {}
            for thread_id, claude_session_id in claude_map.items():
                if isinstance(claude_session_id, str):
                    logger.info(f"  - {thread_id} -> {claude_session_id} (user {user_id})")
                    restored_count += 1

        logger.info(f"Session restoration complete. Restored {restored_count} session mappings.")
