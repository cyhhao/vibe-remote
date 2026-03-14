"""Feishu platform integration E2E tests.

Requires:
- E2E_FEISHU_BOT_B_APP_ID, E2E_FEISHU_BOT_B_APP_SECRET, E2E_FEISHU_CHAT_ID, E2E_FEISHU_BOT_A_ID
- Bot A running in Docker with Feishu connection
- Bot B in the same chat
"""

import pytest

from tests.e2e.integration_cases import PlatformIntegrationTests


@pytest.mark.integration
class TestFeishuIntegration(PlatformIntegrationTests):
    """Run shared integration tests against Feishu platform."""

    @pytest.fixture(autouse=True)
    def _use_driver(self, feishu_driver):
        self._driver = feishu_driver

    @pytest.fixture
    def driver(self):
        return self._driver
