from typing import Optional

from .base import BaseAgent, AgentRequest, AgentMessage
from .claude_agent import ClaudeAgent
from .codex_agent import CodexAgent
from .service import AgentService


def get_agent_display_name(agent_name: Optional[str], fallback: Optional[str] = None) -> str:
    """Return a friendly, title-cased display name for an agent identifier."""
    normalized_map = {
        "claude": "Claude",
        "codex": "Codex",
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
    "ClaudeAgent",
    "CodexAgent",
    "AgentService",
    "get_agent_display_name",
]
