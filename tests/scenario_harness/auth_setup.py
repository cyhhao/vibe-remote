from core.agent_auth_service import AgentAuthService
from tests.scenario_harness.core import BaseScenarioHarness, FakeProcess, ScenarioControllerBase


class AuthSetupScenarioHarness(BaseScenarioHarness):
    """Reusable harness for capability-level auth/setup scenarios."""

    def __init__(self):
        super().__init__(ScenarioControllerBase(default_backend="codex"))
        self.service = AgentAuthService(self.controller)

    def flow(self, backend: str):
        return self.service._flows[f"C1:{backend}"]
