"""Abstract agent interfaces and shared dataclasses."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from modules.im import MessageContext


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

    @abstractmethod
    async def handle_message(self, request: AgentRequest) -> None:
        """Process a user message routed to this agent."""

    async def clear_sessions(self, settings_key: str) -> int:
        """Clear session state for a given settings key. Returns cleared count."""
        return 0

    async def handle_stop(self, request: AgentRequest) -> bool:
        """Attempt to interrupt an in-flight task. Returns True if handled."""
        return False
