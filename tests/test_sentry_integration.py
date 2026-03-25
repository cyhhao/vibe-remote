from __future__ import annotations

import os

from config.v2_config import (
    AgentsConfig,
    RuntimeConfig,
    SentryConfig,
    SlackConfig,
    V2Config,
)
from vibe.sentry_integration import resolve_sentry_options, scrub_data


def _config_with_sentry() -> V2Config:
    return V2Config(
        mode="self_host",
        version="v2",
        slack=SlackConfig(bot_token=""),
        runtime=RuntimeConfig(default_cwd="/tmp/workdir", log_level="INFO"),
        agents=AgentsConfig(),
        sentry=SentryConfig(
            dsn="https://public@example.ingest.sentry.io/1",
            environment="config-env",
            traces_sample_rate=0.0,
            profiles_sample_rate=0.0,
            send_default_pii=False,
        ),
    )


def test_resolve_sentry_options_prefers_environment(monkeypatch):
    monkeypatch.setenv("VIBE_SENTRY_DSN", "https://env@example.ingest.sentry.io/2")
    monkeypatch.setenv("VIBE_SENTRY_ENVIRONMENT", "regression")
    monkeypatch.setenv("VIBE_SENTRY_TRACES_SAMPLE_RATE", "0.25")
    config = _config_with_sentry()

    options = resolve_sentry_options(config)

    assert options is not None
    assert options["dsn"] == "https://env@example.ingest.sentry.io/2"
    assert options["environment"] == "regression"
    assert options["traces_sample_rate"] == 0.25


def test_resolve_sentry_options_returns_none_without_dsn(monkeypatch):
    monkeypatch.delenv("VIBE_SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    config = _config_with_sentry()
    config.sentry = SentryConfig()

    assert resolve_sentry_options(config) is None


def test_scrub_data_redacts_sensitive_values():
    event = {
        "request": {
            "headers": {"Authorization": "Bearer abc", "Cookie": "session=secret"},
            "data": {"bot_token": "xoxb-secret", "nested": [{"client_secret": "shh"}]},
        },
        "message": "token=abc123 and xapp-123456",
    }

    scrubbed = scrub_data(event)

    assert scrubbed["request"]["headers"]["Authorization"] == "[Filtered]"
    assert scrubbed["request"]["headers"]["Cookie"] == "[Filtered]"
    assert scrubbed["request"]["data"]["bot_token"] == "[Filtered]"
    assert scrubbed["request"]["data"]["nested"][0]["client_secret"] == "[Filtered]"
    assert "[Filtered]" in scrubbed["message"]
