import logging
from typing import Dict, Optional

from .base import AgentRequest, BaseAgent

logger = logging.getLogger(__name__)


class AgentService:
    """Registry and dispatcher for agent implementations."""

    def __init__(self, controller):
        self.controller = controller
        self.agents: Dict[str, BaseAgent] = {}
        self.default_agent = "claude"

    def register(self, agent: BaseAgent):
        self.agents[agent.name] = agent
        logger.info(f"Registered agent backend: {agent.name}")

    def get(self, agent_name: Optional[str]) -> BaseAgent:
        target = agent_name or self.default_agent
        if target in self.agents:
            return self.agents[target]
        raise KeyError(target)

    async def handle_message(self, agent_name: str, request: AgentRequest):
        agent = self.get(agent_name)
        await agent.handle_message(request)

    async def clear_sessions(self, settings_key: str) -> Dict[str, int]:
        cleared: Dict[str, int] = {}
        for name, agent in self.agents.items():
            count = await agent.clear_sessions(settings_key)
            if count:
                cleared[name] = count
        return cleared

    async def handle_stop(self, agent_name: str, request: AgentRequest) -> bool:
        agent = self.get(agent_name)
        return await agent.handle_stop(request)
