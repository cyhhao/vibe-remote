"""Fixtures for platform integration E2E tests.

These tests require:
1. Docker container running in full mode (docker-compose.e2e-integration.yml)
2. Platform tokens configured in .env.e2e
3. Bot A and Bot B both present in the test channel
"""

import asyncio
import os

import pytest


def _env(key: str) -> str:
    """Get required env var or empty string."""
    return os.environ.get(key, "")


def _has_slack_config() -> bool:
    return bool(_env("E2E_SLACK_BOT_B_TOKEN") and _env("E2E_SLACK_CHANNEL"))


def _has_discord_config() -> bool:
    return bool(_env("E2E_DISCORD_BOT_B_TOKEN") and _env("E2E_DISCORD_CHANNEL"))


def _has_feishu_config() -> bool:
    return bool(_env("E2E_FEISHU_BOT_B_APP_ID") and _env("E2E_FEISHU_BOT_B_APP_SECRET") and _env("E2E_FEISHU_CHAT_ID"))


@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def slack_driver(event_loop):
    """Session-scoped Slack driver. Skips if not configured."""
    if not _has_slack_config():
        pytest.skip("Slack E2E not configured (missing E2E_SLACK_BOT_B_TOKEN or E2E_SLACK_CHANNEL)")

    from tests.e2e.drivers.slack_driver import SlackDriver

    driver = SlackDriver()
    event_loop.run_until_complete(driver.setup())
    yield driver
    event_loop.run_until_complete(driver.teardown())


@pytest.fixture(scope="session")
def discord_driver(event_loop):
    """Session-scoped Discord driver. Skips if not configured."""
    if not _has_discord_config():
        pytest.skip("Discord E2E not configured (missing E2E_DISCORD_BOT_B_TOKEN or E2E_DISCORD_CHANNEL)")

    from tests.e2e.drivers.discord_driver import DiscordDriver

    driver = DiscordDriver()
    event_loop.run_until_complete(driver.setup())
    yield driver
    event_loop.run_until_complete(driver.teardown())


@pytest.fixture(scope="session")
def feishu_driver(event_loop):
    """Session-scoped Feishu driver. Skips if not configured."""
    if not _has_feishu_config():
        pytest.skip("Feishu E2E not configured (missing E2E_FEISHU_BOT_B_* or E2E_FEISHU_CHAT_ID)")

    from tests.e2e.drivers.feishu_driver import FeishuDriver

    driver = FeishuDriver()
    event_loop.run_until_complete(driver.setup())
    yield driver
    event_loop.run_until_complete(driver.teardown())
