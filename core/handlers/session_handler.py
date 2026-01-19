"""Session management handlers for Claude SDK sessions"""

import os
import logging
from typing import Optional, Dict, Any, Tuple
from modules.im import MessageContext
from claude_code_sdk import ClaudeSDKClient, ClaudeCodeOptions

logger = logging.getLogger(__name__)


class SessionHandler:
    """Handles all session-related operations"""
    
    def __init__(self, controller):
        """Initialize with reference to main controller"""
        self.controller = controller
        self.config = controller.config
        self.im_client = controller.im_client
        self.session_manager = controller.session_manager
        self.settings_manager = controller.settings_manager
        self.formatter = controller.im_client.formatter
        self.claude_sessions = controller.claude_sessions
        self.receiver_tasks = controller.receiver_tasks
        self.stored_session_mappings = controller.stored_session_mappings
    
    def _get_settings_key(self, context: MessageContext) -> str:
        """Get settings key - delegate to controller"""
        return self.controller._get_settings_key(context)
    
    def get_base_session_id(self, context: MessageContext) -> str:
        """Get base session ID based on platform and context (without path)"""
        # Slack only in V2; always use thread ID
        return f"slack_{context.thread_id}"
    
    def get_working_path(self, context: MessageContext) -> str:
        """Get working directory - delegate to controller's get_cwd"""
        return self.controller.get_cwd(context)
    
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
        stored_claude_session_id = self.settings_manager.get_claude_session_id(
            settings_key, base_session_id
        )

        if composite_key in self.claude_sessions and not subagent_name:
            logger.info(f"Using existing Claude SDK client for {base_session_id} at {working_path}")
            return self.claude_sessions[composite_key]

        if subagent_name:
            cached_base = f"{base_session_id}:{subagent_name}"
            cached_key = f"{cached_base}:{working_path}"
            cached_session_id = self.settings_manager.get_agent_session_id(
                settings_key,
                cached_base,
                agent_name="claude",
            )
            if cached_key in self.claude_sessions:
                logger.info(
                    "Using Claude subagent session for %s at %s", cached_base, working_path
                )
                return self.claude_sessions[cached_key]
            if cached_session_id:
                stored_claude_session_id = cached_session_id
                composite_key = cached_key
                base_session_id = cached_base
            if composite_key in self.claude_sessions:
                logger.info(
                    "Using Claude subagent session for %s at %s", cached_base, working_path
                )
                return self.claude_sessions[composite_key]

        # Ensure working directory exists
        if not os.path.exists(working_path):
            try:
                os.makedirs(working_path, exist_ok=True)
                logger.info(f"Created working directory: {working_path}")
            except Exception as e:
                logger.error(f"Failed to create working directory {working_path}: {e}")
                working_path = os.getcwd()
        
        # Create options for Claude client
        extra_args = {}
        if subagent_name:
            extra_args["agent"] = subagent_name
        if subagent_model:
            extra_args["model"] = subagent_model
        if subagent_reasoning_effort:
            extra_args["reasoning-effort"] = subagent_reasoning_effort

        options = ClaudeCodeOptions(
            permission_mode=self.config.claude.permission_mode,
            cwd=working_path,
            system_prompt=self.config.claude.system_prompt,
            resume=stored_claude_session_id if stored_claude_session_id else None,
            extra_args=extra_args,
        )
        
        # Log session creation details
        logger.info(f"Creating Claude client for {base_session_id} at {working_path}")
        logger.info(f"  Working directory: {working_path}")
        logger.info(f"  Resume session ID: {stored_claude_session_id}")
        logger.info(f"  Options.resume: {options.resume}")
        if subagent_name:
            logger.info(f"  Subagent: {subagent_name}")
        
        # Log if we're resuming a session
        if stored_claude_session_id:
            logger.info(f"Attempting to resume Claude session {stored_claude_session_id}")
        else:
            logger.info(f"Creating new Claude session")
        
        # Create new Claude client
        client = ClaudeSDKClient(options=options)

        # Log the actual options being used
        logger.info("ClaudeCodeOptions details:")
        logger.info(f"  - permission_mode: {options.permission_mode}")
        logger.info(f"  - cwd: {options.cwd}")
        logger.info(f"  - system_prompt: {options.system_prompt}")
        logger.info(f"  - resume: {options.resume}")
        logger.info(f"  - continue_conversation: {options.continue_conversation}")
        if subagent_name:
            logger.info(f"  - subagent: {subagent_name}")

        # Connect the client
        await client.connect()

        self.claude_sessions[composite_key] = client
        logger.info(f"Created new Claude SDK client for {base_session_id} at {working_path}")
        
        return client
    
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
            await self.im_client.send_message(
                context,
                self.formatter.format_error(
                    "Session error detected. Session has been reset. Please try your message again."
                )
            )
        elif "Session is broken" in error_msg or "Connection closed" in error_msg or "Connection lost" in error_msg:
            logger.error(f"Session {composite_key} is broken - cleaning up")
            await self.cleanup_session(composite_key)
            
            # Notify user
            await self.im_client.send_message(
                context,
                self.formatter.format_error(
                    "Connection to Claude was lost. Please try your message again."
                )
            )
        else:
            # Generic error handling
            logger.error(f"Error in session {composite_key}: {error}")
            await self.im_client.send_message(
                context,
                self.formatter.format_error(f"An error occurred: {error_msg}")
            )
    
    def capture_session_id(self, base_session_id: str, claude_session_id: str, settings_key: str):
        """Capture and store Claude session ID mapping"""
        # Persist to settings (settings_key is channel_id for Slack)
        self.settings_manager.set_session_mapping(settings_key, base_session_id, claude_session_id)
        
        logger.info(f"Captured Claude session_id: {claude_session_id} for {base_session_id}")
    
    def restore_session_mappings(self):
        """Restore session mappings from settings on startup"""
        logger.info("Initializing session mappings from saved settings...")
        
        session_state = self.settings_manager.sessions_store.state.session_mappings

        restored_count = 0
        for user_id, agent_map in session_state.items():
            claude_map = agent_map.get("claude", {}) if isinstance(agent_map, dict) else {}
            for thread_id, claude_session_id in claude_map.items():
                if isinstance(claude_session_id, str):
                    logger.info(
                        f"  - {thread_id} -> {claude_session_id} (user {user_id})"
                    )
                    restored_count += 1

        logger.info(f"Session restoration complete. Restored {restored_count} session mappings.")
