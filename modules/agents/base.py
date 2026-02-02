"""Abstract agent interfaces and shared dataclasses."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from modules.im import MessageContext
from modules.im.base import FileAttachment


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

    def _calculate_duration_ms(self, started_at: Optional[float]) -> int:
        if not started_at:
            return 0
        elapsed = time.monotonic() - started_at
        return max(0, int(elapsed * 1000))

    async def _remove_ack_reaction(self, request: AgentRequest) -> None:
        """Remove the acknowledgement reaction from user's message.

        Called after sending result message to clean up the 👀 reaction.
        """
        if request.ack_reaction_message_id and request.ack_reaction_emoji:
            try:
                await self.im_client.remove_reaction(
                    request.context,
                    request.ack_reaction_message_id,
                    request.ack_reaction_emoji,
                )
            except Exception as err:
                import logging

                logging.getLogger(__name__).debug(f"Failed to remove reaction ack: {err}")
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
        if duration_ms is None:
            duration_ms = self._calculate_duration_ms(started_at)
        formatted = self.im_client.formatter.format_result_message(subtype or "", duration_ms, result_text)
        if suffix:
            formatted = f"{formatted}\n{suffix}"
        await self.controller.emit_agent_message(context, "result", formatted, parse_mode=parse_mode)
        # Remove ack reaction after result is sent
        if request:
            await self._remove_ack_reaction(request)

    @abstractmethod
    async def handle_message(self, request: AgentRequest) -> None:
        """Process a user message routed to this agent."""

    async def clear_sessions(self, settings_key: str) -> int:
        """Clear session state for a given settings key. Returns cleared count."""
        return 0

    async def handle_stop(self, request: AgentRequest) -> bool:
        """Attempt to interrupt an in-flight task. Returns True if handled."""
        return False
