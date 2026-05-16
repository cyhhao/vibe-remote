from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from modules.agents.service import AgentService


def test_agent_service_dispatches_runtime_config_refresh() -> None:
    service = AgentService(controller=SimpleNamespace())
    runtime_config = object()
    agent = SimpleNamespace(name="codex", refresh_runtime_config=AsyncMock())
    service.register(agent)

    handled = asyncio.run(service.refresh_runtime_config("codex", runtime_config))

    assert handled is True
    agent.refresh_runtime_config.assert_awaited_once_with(runtime_config)


def test_agent_service_reports_missing_runtime_refresh_contract() -> None:
    service = AgentService(controller=SimpleNamespace())
    service.register(SimpleNamespace(name="codex"))

    assert asyncio.run(service.refresh_runtime_config("codex", object())) is False
    assert asyncio.run(service.refresh_runtime_config("claude", object())) is False
