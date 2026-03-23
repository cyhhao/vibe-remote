"""Session management handlers for Claude SDK sessions"""

import logging
import os
from typing import Optional, Dict, Any, Tuple
from modules.im import MessageContext
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

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

    def get_base_session_id(self, context: MessageContext) -> str:
        """Get base session ID based on platform and context (without path)"""
        platform = getattr(self.config, "platform", "slack")
        is_dm = bool((context.platform_specific or {}).get("is_dm", False))
        if is_dm:
            use_dm_threads = False
            im_client = getattr(self.controller, "im_client", None)
            if im_client and hasattr(im_client, "should_use_thread_for_dm_session"):
                use_dm_threads = bool(im_client.should_use_thread_for_dm_session())

            if use_dm_threads:
                base_id = context.thread_id or context.message_id or context.channel_id or context.user_id
            else:
                base_id = context.channel_id or context.user_id
        else:
            base_id = context.thread_id or context.message_id or context.channel_id
        return f"{platform}_{base_id}"

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

        expanded = os.path.expanduser(normalized)
        if normalized == "claude":
            from vibe.api import resolve_cli_path

            return resolve_cli_path(normalized)

        return expanded

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

    def get_session_info(self, context: MessageContext) -> Tuple[str, str, str]:
        """Get session info: base_session_id, working_path, and composite_key"""
        base_session_id = self.get_base_session_id(context)
        working_path = self.get_working_path(context)  # Pass context to get user's custom_cwd
        # Create composite key for internal storage
        composite_key = f"{base_session_id}:{working_path}"
        return base_session_id, working_path, composite_key

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
        stored_claude_session_id = self.sessions.get_claude_session_id(settings_key, base_session_id)

        # Read configuration overrides using settings_key (user_id for DM, channel_id for channels)
        channel_settings = self.settings_manager.get_channel_settings(settings_key)
        routing = channel_settings.routing if channel_settings else None

        # Priority: subagent params > channel config > agent frontmatter > global default
        # Note: agent frontmatter model is applied later after loading agent file
        effective_agent = subagent_name or (routing.claude_agent if routing else None)
        # Store explicit model override (not including default yet)
        explicit_model = subagent_model or (routing.claude_model if routing else None)
        explicit_effort = subagent_reasoning_effort or (routing.claude_reasoning_effort if routing else None)

        if composite_key in self.claude_sessions and not effective_agent:
            logger.info(f"Using existing Claude SDK client for {base_session_id} at {working_path}")
            return self.claude_sessions[composite_key]

        if effective_agent:
            cached_base = f"{base_session_id}:{effective_agent}"
            cached_key = f"{cached_base}:{working_path}"
            cached_session_id = self.sessions.get_agent_session_id(
                settings_key,
                cached_base,
                agent_name="claude",
            )
            if cached_key in self.claude_sessions:
                logger.info("Using Claude subagent session for %s at %s", cached_base, working_path)
                return self.claude_sessions[cached_key]
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

            reply_prompt = build_reply_enhancements_prompt(include_quick_replies=self.config.platform != "wechat")

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
                thread_id=target_thread or None,
                platform_specific={"is_dm": is_dm},
            )

            settings_key = self._get_settings_key(context)
            current_routing = self.settings_manager.get_channel_routing(settings_key)

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

            mapped_thread = target_thread or confirmation_ts
            mapping_context = MessageContext(
                user_id=user_id,
                channel_id=context.channel_id,
                thread_id=mapped_thread,
                message_id=confirmation_ts,
                platform_specific={"is_dm": is_dm},
            )
            base_session_id = self.get_base_session_id(mapping_context)

            self.sessions.set_agent_session_mapping(settings_key, agent, base_session_id, session_id)
            self.sessions.mark_thread_active(user_id, context.channel_id, mapped_thread)
        except Exception as e:
            logger.error(f"Error resuming session: {e}", exc_info=True)
            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id or user_id,
                thread_id=thread_id or None,
                platform_specific={"is_dm": is_dm},
            )
            await self.im_client.send_message(
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

    async def handle_session_error(self, composite_key: str, context: MessageContext, error: Exception):
        """Handle session-related errors"""
        error_msg = str(error)

        # Check for specific error types
        if "read() called while another coroutine" in error_msg:
            logger.error(f"Session {composite_key} has concurrent read error - cleaning up")
            await self.cleanup_session(composite_key)

            # Notify user and suggest retry
            await self.im_client.send_message(context, self.formatter.format_error(self._t("error.sessionReset")))
        elif "Session is broken" in error_msg or "Connection closed" in error_msg or "Connection lost" in error_msg:
            logger.error(f"Session {composite_key} is broken - cleaning up")
            await self.cleanup_session(composite_key)

            # Notify user
            await self.im_client.send_message(
                context, self.formatter.format_error(self._t("error.sessionConnectionLost"))
            )
        else:
            # Generic error handling
            logger.error(f"Error in session {composite_key}: {error}")
            await self.im_client.send_message(
                context, self.formatter.format_error(self._t("error.sessionGeneric", error=error_msg))
            )

    def capture_session_id(self, base_session_id: str, claude_session_id: str, settings_key: str):
        """Capture and store Claude session ID mapping"""
        # Persist to settings (settings_key is channel_id for Slack)
        self.sessions.set_session_mapping(settings_key, base_session_id, claude_session_id)

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
