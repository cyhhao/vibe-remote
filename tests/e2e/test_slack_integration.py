"""Slack platform integration E2E tests.

Requires:
- E2E_SLACK_BOT_B_TOKEN, E2E_SLACK_CHANNEL, E2E_SLACK_BOT_A_ID
- Bot A running in Docker with Slack connection
- Bot B in the same channel
"""

import pytest

from tests.e2e.integration_cases import PlatformIntegrationTests


@pytest.mark.integration
class TestSlackIntegration(PlatformIntegrationTests):
    """Run shared integration tests against Slack platform."""

    @pytest.fixture(autouse=True)
    def _use_driver(self, slack_driver):
        self._driver = slack_driver

    @pytest.fixture
    def driver(self):
        return self._driver
