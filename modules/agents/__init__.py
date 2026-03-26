from typing import Optional

from .base import BaseAgent, AgentRequest, AgentMessage
from .service import AgentService


def get_agent_display_name(agent_name: Optional[str], fallback: Optional[str] = None) -> str:
    normalized_map = {
        "claude": "Claude",
        "codex": "Codex",
        "opencode": "OpenCode",
    }

    candidate = (agent_name or fallback or "Agent").strip()
    if not candidate:
        candidate = "Agent"

    normalized = candidate.lower()
    friendly = normalized_map.get(normalized)
    if friendly:
        return friendly

    return candidate.replace("_", " ").title()


__all__ = [
    "AgentMessage",
    "AgentRequest",
    "BaseAgent",
    "AgentService",
    "get_agent_display_name",
]
