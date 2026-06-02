import logging
from typing import Any, Dict, Optional

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
        # INBOUND status chokepoint (one of exactly two — the other is the outbound
        # MessageDispatcher.emit_agent_message). Every turn, every source (chat /
        # scheduled / Show Page), every backend funnels through here, so this is the
        # single place that marks an avibe session "running". The matching idle /
        # failed is written by the outbound terminal result. Non-avibe turns carry
        # no workbench session id and are skipped.
        manager = getattr(self.controller, "session_turns", None)
        if manager is not None:
            manager.on_running(request.context)
        await agent.handle_message(request)

    async def clear_sessions(self, session_key: str) -> Dict[str, int]:
        cleared: Dict[str, int] = {}
        for name, agent in self.agents.items():
            count = await agent.clear_sessions(session_key)
            if count:
                cleared[name] = count
        return cleared

    async def handle_stop(self, agent_name: str, request: AgentRequest) -> bool:
        agent = self.get(agent_name)
        return await agent.handle_stop(request)

    async def refresh_runtime_config(self, agent_name: str, runtime_config: Any) -> bool:
        """Refresh a backend's live runtime state from the latest config.

        Backend adapters own their cached transports/sessions, so the service
        centralizes dispatch while adapters decide how to apply the new
        runtime config. Returns ``False`` when the backend is not registered or
        does not expose the refresh contract.
        """
        agent = self.agents.get(agent_name)
        if agent is None:
            return False
        refresh = getattr(agent, "refresh_runtime_config", None)
        if not callable(refresh):
            return False
        await refresh(runtime_config)
        return True
