"""Abstract agent interfaces and shared dataclasses."""

from __future__ import annotations

import logging
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from modules.im import MessageContext, InlineButton, InlineKeyboard

logger = logging.getLogger(__name__)


def get_git_branch(working_path: str) -> Optional[str]:
    """Get current git branch for a working directory."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=working_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def has_open_pr_for_branch(working_path: str, branch: str) -> bool:
    """Check if the given branch already has an open PR using gh CLI."""
    if not branch or branch in ("main", "master"):
        return False
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"],
            cwd=working_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            import json
            prs = json.loads(result.stdout.strip() or "[]")
            return len(prs) > 0
    except Exception:
        pass
    return False


def checkout_main_branch(working_path: str) -> Optional[str]:
    """Checkout main or master branch. Returns the branch name if successful."""
    for main_branch in ("main", "master"):
        try:
            result = subprocess.run(
                ["git", "checkout", main_branch],
                cwd=working_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return main_branch
        except Exception:
            pass
    return None


@dataclass
class AgentRequest:
    """Normalized agent invocation request."""

    context: MessageContext
    message: str
    working_path: str
    base_session_id: str
    composite_session_id: str
    settings_key: str
    ack_message_id: Optional[str] = None
    last_agent_message: Optional[str] = None
    last_agent_message_parse_mode: Optional[str] = None
    started_at: float = field(default_factory=time.monotonic)
    # Flag to indicate this is a PR creation request, show Merge PR button after success
    is_pr_creation: bool = False
    # Status updater for periodic progress updates (managed externally)
    status_updater: Optional[Any] = None


@dataclass
class AgentMessage:
    """Normalized message emitted by an agent implementation."""

    text: str
    message_type: str = "assistant"
    parse_mode: str = "markdown"
    metadata: Optional[Dict[str, Any]] = None


class BaseAgent(ABC):
    """Abstract base class for all agent implementations."""

    name: str

    def __init__(self, controller):
        self.controller = controller
        self.config = controller.config
        self.im_client = controller.im_client
        self.settings_manager = controller.settings_manager

    def _calculate_duration_ms(self, started_at: Optional[float]) -> int:
        if not started_at:
            return 0
        elapsed = time.monotonic() - started_at
        return max(0, int(elapsed * 1000))

    async def emit_result_message(
        self,
        context: MessageContext,
        result_text: Optional[str],
        subtype: str = "success",
        duration_ms: Optional[int] = None,
        started_at: Optional[float] = None,
        parse_mode: str = "markdown",
        suffix: Optional[str] = None,
        working_path: Optional[str] = None,
        composite_session_id: Optional[str] = None,
    ) -> None:
        if duration_ms is None:
            duration_ms = self._calculate_duration_ms(started_at)
        formatted = self.im_client.formatter.format_result_message(
            subtype or "", duration_ms, result_text
        )
        if suffix:
            formatted = f"{formatted}\n{suffix}"
        await self.controller.emit_agent_message(
            context, "result", formatted, parse_mode=parse_mode
        )

        # Check if this session was creating a PR
        is_pr_creation = False
        if composite_session_id and composite_session_id in self.controller.pending_pr_sessions:
            is_pr_creation = True
            # Clear the flag
            del self.controller.pending_pr_sessions[composite_session_id]

        # After PR creation success, show Merge PR and Close PR buttons
        if subtype == "success" and is_pr_creation:
            await self._emit_pr_actions(context)
        # After regular result message, show Create PR and Codex Review buttons
        elif subtype == "success" and working_path:
            await self._emit_post_task_actions(context, working_path)

    async def _emit_post_task_actions(
        self, context: MessageContext, working_path: str, base_session_id: str = None
    ) -> None:
        """Emit post-task action buttons with branch info.

        Shows different buttons based on PR state:
        - If branch has an open PR: show Merge PR / Close PR buttons
        - If branch has no PR: show Create PR / Codex Review buttons
        - Skip if on main/master branch or not a git repo
        - Validates thread-branch isolation to prevent cross-thread button confusion
        """
        branch = get_git_branch(working_path)
        if not branch:
            # Not a git repo, skip the buttons
            return

        # Skip if on main/master branch
        if branch in ("main", "master"):
            return

        # Get settings key for thread branch tracking
        settings_key = self.controller._get_settings_key(context)

        # Get the base_session_id if not provided
        if not base_session_id:
            base_session_id = self.controller.session_handler.get_base_session_id(context)

        # Track or validate thread-branch association
        tracked_branch = self.settings_manager.get_thread_branch(settings_key, base_session_id)

        if tracked_branch:
            # This thread has an associated branch - validate it matches
            if tracked_branch != branch:
                # Branch mismatch! The working directory has a different branch
                # than what this thread was working on. This likely means
                # another thread changed the branch.
                logger.warning(
                    f"Branch mismatch for session {base_session_id}: "
                    f"expected {tracked_branch}, got {branch}. "
                    f"Skipping action buttons to prevent cross-thread confusion."
                )
                formatter = self.im_client.formatter
                warning_msg = (
                    f"⚠️ Branch mismatch detected!\n"
                    f"This thread was working on {formatter.format_code_inline(tracked_branch)}, "
                    f"but the working directory is now on {formatter.format_code_inline(branch)}.\n"
                    f"Please check that the correct branch is checked out."
                )
                target_context = self._get_target_context(context)
                await self.im_client.send_message(target_context, warning_msg)
                return
        else:
            # First time seeing a non-main branch in this thread - track it
            self.settings_manager.set_thread_branch(settings_key, base_session_id, branch)
            logger.info(f"Associated thread {base_session_id} with branch {branch}")

        # Build message with branch info
        formatter = self.im_client.formatter
        branch_info = f"🌿 Current Branch: {formatter.format_code_inline(branch)}"

        # Check if branch already has an open PR
        if has_open_pr_for_branch(working_path, branch):
            # Show Merge PR, Close PR, and Codex Review buttons
            buttons = [
                [
                    InlineButton(text="✅ Merge PR", callback_data="cmd_merge_pr"),
                    InlineButton(text="❌ Close PR", callback_data="cmd_close_pr"),
                    InlineButton(text="🔍 Codex Review", callback_data="cmd_codex_review"),
                ]
            ]
            branch_info = f"🌿 Branch: {formatter.format_code_inline(branch)} (has open PR)"
        else:
            # Show Create PR and Codex Review buttons
            buttons = [
                [
                    InlineButton(text="🚀 Create PR", callback_data="cmd_create_pr"),
                    InlineButton(text="🔍 Codex Review", callback_data="cmd_codex_review"),
                ]
            ]

        keyboard = InlineKeyboard(buttons=buttons)

        target_context = self._get_target_context(context)
        await self.im_client.send_message_with_buttons(
            target_context, branch_info, keyboard
        )

    async def _emit_pr_actions(self, context: MessageContext) -> None:
        """Emit PR action buttons (Merge PR, Close PR, Codex Review) after PR creation."""
        buttons = [
            [
                InlineButton(text="✅ Merge PR", callback_data="cmd_merge_pr"),
                InlineButton(text="❌ Close PR", callback_data="cmd_close_pr"),
                InlineButton(text="🔍 Codex Review", callback_data="cmd_codex_review"),
            ]
        ]
        keyboard = InlineKeyboard(buttons=buttons)

        target_context = self._get_target_context(context)
        await self.im_client.send_message_with_buttons(
            target_context, "🎉 PR created! What would you like to do next?", keyboard
        )

    def _get_target_context(self, context: MessageContext) -> MessageContext:
        """Return context for sending messages (respect Slack thread replies)."""
        if self.im_client.should_use_thread_for_reply() and context.thread_id:
            return MessageContext(
                user_id=context.user_id,
                channel_id=context.channel_id,
                thread_id=context.thread_id,
                message_id=context.message_id,
                platform_specific=context.platform_specific,
            )
        return context

    @abstractmethod
    async def handle_message(self, request: AgentRequest) -> None:
        """Process a user message routed to this agent."""

    async def clear_sessions(self, settings_key: str) -> int:
        """Clear session state for a given settings key. Returns cleared count."""
        return 0

    async def handle_stop(self, request: AgentRequest) -> bool:
        """Attempt to interrupt an in-flight task. Returns True if handled."""
        return False
