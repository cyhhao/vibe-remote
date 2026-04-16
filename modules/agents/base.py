"""Abstract agent interfaces and shared dataclasses."""

from __future__ import annotations

import logging
import time
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from modules.im import MessageContext
from modules.im.base import FileAttachment
from core.reply_enhancer import strip_silent_blocks

logger = logging.getLogger(__name__)


@dataclass
class AgentRequest:
    """Normalized agent invocation request."""

    context: MessageContext
    message: str
    working_path: str
    base_session_id: str
    composite_session_id: str
    session_key: str
    ack_message_id: Optional[str] = None
    subagent_name: Optional[str] = None
    subagent_key: Optional[str] = None
    subagent_model: Optional[str] = None
    subagent_reasoning_effort: Optional[str] = None
    last_agent_message: Optional[str] = None
    last_agent_message_parse_mode: Optional[str] = None
    started_at: float = field(default_factory=time.monotonic)
    # Reaction ack: emoji added to user's message, to be removed when result is sent
    ack_reaction_message_id: Optional[str] = None
    ack_reaction_emoji: Optional[str] = None
    typing_indicator_active: bool = False
    typing_indicator_task: Optional[Any] = None
    # File attachments (downloaded or with URLs for download)
    files: Optional[List[FileAttachment]] = None


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
        self.sessions = (
            getattr(controller, "sessions", None)
            or getattr(controller.settings_manager, "sessions", None)
            or controller.settings_manager
        )

    def _calculate_duration_ms(self, started_at: Optional[float]) -> int:
        if not started_at:
            return 0
        elapsed = time.monotonic() - started_at
        return max(0, int(elapsed * 1000))

    def _get_im_client(self, context: MessageContext):
        getter = getattr(self.controller, "get_im_client_for_context", None)
        if callable(getter):
            return getter(context)
        return self.im_client

    def _get_formatter(self, context: MessageContext):
        return getattr(self._get_im_client(context), "formatter", self.im_client.formatter)

    async def _remove_ack_reaction(self, request: AgentRequest) -> None:
        """Remove the acknowledgement reaction / typing indicator.

        Called after sending result message or on terminal error to clean up
        the 👀 reaction.  This is the **single** implementation — subclasses
        should NOT override it.  The guard (check-then-clear) is idempotent so
        calling it more than once is harmless.
        """
        typing_task = request.typing_indicator_task
        if typing_task is not None:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("Failed to stop typing keepalive task", exc_info=True)
            finally:
                request.typing_indicator_task = None

        if request.typing_indicator_active:
            try:
                await self._get_im_client(request.context).clear_typing_indicator(request.context)
            except Exception as err:
                logger.debug(f"Failed to clear typing indicator: {err}")
            finally:
                request.typing_indicator_active = False

        if request.ack_reaction_message_id and request.ack_reaction_emoji:
            try:
                await self._get_im_client(request.context).remove_reaction(
                    request.context,
                    request.ack_reaction_message_id,
                    request.ack_reaction_emoji,
                )
            except Exception as err:
                logger.debug(f"Failed to remove reaction ack: {err}")
            finally:
                request.ack_reaction_message_id = None
                request.ack_reaction_emoji = None

    async def emit_result_message(
        self,
        context: MessageContext,
        result_text: Optional[str],
        subtype: str = "success",
        duration_ms: Optional[int] = None,
        started_at: Optional[float] = None,
        parse_mode: str = "markdown",
        suffix: Optional[str] = None,
        request: Optional[AgentRequest] = None,
    ) -> None:
        show_duration = getattr(self.config, "show_duration", True)
        if duration_ms is None:
            duration_ms = self._calculate_duration_ms(started_at)

        raw_result = result_text or ""
        raw_suffix = suffix or ""
        visible_result = strip_silent_blocks(raw_result)
        visible_suffix = strip_silent_blocks(raw_suffix) if raw_suffix else None
        has_silent_directive = "<silent" in raw_result.lower() or "<silent" in raw_suffix.lower()

        if has_silent_directive and not visible_result.strip() and not (visible_suffix or "").strip():
            await self.controller.emit_agent_message(context, "result", "", parse_mode=parse_mode)
            if request:
                await self._remove_ack_reaction(request)
            return

        # When show_duration is disabled, skip the entire result line
        # unless there is actual result_text or suffix to deliver.
        if not show_duration:
            parts = []
            if visible_result and visible_result.strip():
                parts.append(visible_result)
            if visible_suffix:
                parts.append(visible_suffix)
            if parts:
                formatted = "\n".join(parts)
                await self.controller.emit_agent_message(context, "result", formatted, parse_mode=parse_mode)
        else:
            formatted = self._get_formatter(context).format_result_message(
                subtype or "",
                duration_ms,
                visible_result,
                show_duration=True,
            )
            if visible_suffix:
                formatted = f"{formatted}\n{visible_suffix}"
            await self.controller.emit_agent_message(context, "result", formatted, parse_mode=parse_mode)

        # Remove ack reaction after result is sent
        if request:
            await self._remove_ack_reaction(request)

    @abstractmethod
    async def handle_message(self, request: AgentRequest) -> None:
        """Process a user message routed to this agent."""

    async def clear_sessions(self, session_key: str) -> int:
        """Clear session state for a given session scope key. Returns cleared count."""
        return 0

    async def prepare_resume_binding(
        self,
        *,
        base_session_id: str,
        session_key: str,
        working_path: str,
    ) -> None:
        """Prepare backend runtime before binding a resumed session."""
        return None

    async def handle_stop(self, request: AgentRequest) -> bool:
        """Attempt to interrupt an in-flight task. Returns True if handled."""
        return False
