"""Contract tests for ``core.services.settings``.

C3 of Plan 1. Pins the public surface so the CLI and UI server can both
import from one place and the legacy ``V2Config.load`` / ``SettingsStore.
get_instance`` divergence stays gone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import SettingsStore
from config.v2_config import V2Config
from core.services import settings as settings_service


@pytest.fixture()
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()
    yield tmp_path
    SettingsStore.reset_instance()


def test_public_surface_is_stable():
    expected = {
        "load_config",
        "get_settings_store",
        "reload_settings_store",
        "reset_settings_store",
    }
    assert set(settings_service.__all__) == expected
    for name in expected:
        assert callable(getattr(settings_service, name))


def test_load_config_requires_file_by_default(isolated_state):
    with pytest.raises(FileNotFoundError):
        settings_service.load_config()


def test_load_config_seeds_default_when_factory_given(isolated_state, tmp_path):
    target = tmp_path / "config.json"

    def _factory() -> V2Config:
        # Minimal valid V2Config — mirrors what CLI's _default_config does.
        from config.v2_config import (
            AgentsConfig,
            ClaudeConfig,
            CodexConfig,
            OpenCodeConfig,
            RuntimeConfig,
            SlackConfig,
        )

        return V2Config(
            mode="self_host",
            version="v2",
            slack=SlackConfig(bot_token="", app_token=""),
            runtime=RuntimeConfig(default_cwd=str(tmp_path / "work")),
            agents=AgentsConfig(
                default_backend="opencode",
                opencode=OpenCodeConfig(enabled=True, cli_path="opencode"),
                claude=ClaudeConfig(enabled=True, cli_path="claude"),
                codex=CodexConfig(enabled=False, cli_path="codex"),
            ),
        )

    assert not target.exists()
    config = settings_service.load_config(target, default_factory=_factory)
    assert target.exists(), "factory result should be persisted to disk"
    assert config.version == "v2"
    # Reload returns the persisted file, not a fresh factory invocation.
    again = settings_service.load_config(target)
    assert again.version == "v2"


def test_get_settings_store_returns_singleton(isolated_state):
    a = settings_service.get_settings_store()
    b = settings_service.get_settings_store()
    assert a is b


def test_reset_settings_store_drops_singleton(isolated_state):
    a = settings_service.get_settings_store()
    settings_service.reset_settings_store()
    b = settings_service.get_settings_store()
    assert a is not b, "reset must release the previous singleton"


def test_reload_settings_store_returns_same_instance(isolated_state):
    a = settings_service.get_settings_store()
    b = settings_service.reload_settings_store()
    assert a is b
