"""Discord platform integration E2E tests.

Requires:
- E2E_DISCORD_BOT_B_TOKEN, E2E_DISCORD_CHANNEL, E2E_DISCORD_BOT_A_ID
- Bot A running in Docker with Discord connection
- Bot B in the same server/channel
"""

import pytest

from tests.e2e.integration_cases import PlatformIntegrationTests


@pytest.mark.integration
class TestDiscordIntegration(PlatformIntegrationTests):
    """Run shared integration tests against Discord platform."""

    @pytest.fixture(autouse=True)
    def _use_driver(self, discord_driver):
        self._driver = discord_driver

    @pytest.fixture
    def driver(self):
        return self._driver
