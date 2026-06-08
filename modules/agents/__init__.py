from typing import Optional

from .base import BaseAgent, AgentRequest, AgentMessage
from .catalog import display_name_for_backend
from .service import AgentService


def get_agent_display_name(agent_name: Optional[str], fallback: Optional[str] = None) -> str:
    """Return the display name for an agent.

    Given an agent name or a fallback, this function returns the display name
    by formatting it for the backend. If both are missing or empty, it defaults
    to "Agent".

    Args:
        agent_name: Optional name of the agent.
        fallback: Optional fallback name to use if agent_name is not provided.

    Returns:
        str: The formatted display name.
    """
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
