from typing import Optional

from .base import BaseAgent, AgentRequest, AgentMessage
from .catalog import display_name_for_backend
from .service import AgentService


def get_agent_display_name(agent_name: Optional[str], fallback: Optional[str] = None) -> str:
    candidate = (agent_name or fallback or "Agent").strip()
    if not candidate:
        candidate = "Agent"
    return display_name_for_backend(candidate.lower())


__all__ = [
    "AgentMessage",
    "AgentRequest",
    "BaseAgent",
    "AgentService",
    "get_agent_display_name",
]
