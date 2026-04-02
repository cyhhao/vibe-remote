from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_settings import SettingsStore, UserSettings
from config.v2_config import UpdateConfig
from core import update_checker
from core.update_checker import UpdateChecker


class _StubSettingsManager:
    def __init__(self, store):
        self._store = store

    def get_store(self):
        return self._store


class _StubController:
    def __init__(self, store):
        self.settings_manager = _StubSettingsManager(store)
        self.config = type("Config", (), {"platform": "slack"})()
        self.im_client = object()


def test_get_admin_user_ids_includes_all_platforms(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()
    store = SettingsStore.get_instance()
    store.set_users_for_platform("slack", {"U1": UserSettings(display_name="Slack", is_admin=True)})
    store.set_users_for_platform("discord", {"D1": UserSettings(display_name="Discord", is_admin=True)})
    store.save()

    checker = UpdateChecker(_StubController(store), UpdateConfig())

    admin_ids = checker._get_admin_user_ids()

    assert set(admin_ids) == {"slack::U1", "discord::D1"}


def test_stop_returns_cancellable_task(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()

    async def run_test():
        checker = UpdateChecker(_StubController(SettingsStore.get_instance()), UpdateConfig(check_interval_minutes=1))
        checker.start()
        await asyncio.sleep(0)
        task = checker.stop()
        assert task is not None
        await checker.wait_stopped(task)
        assert task.done()

    asyncio.run(run_test())


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fetch_pypi_version_sync_ignores_prerelease_for_stable_current(monkeypatch):
    payload = b"""
    {
      "info": {"version": "2.2.8rc1"},
      "releases": {
        "2.2.7": [{}],
        "2.2.8rc1": [{}]
      }
    }
    """

    with patch.object(update_checker.urllib.request, "urlopen", return_value=_FakeResponse(payload)):
        monkeypatch.setattr("vibe.__version__", "2.2.7", raising=False)
        info = update_checker._fetch_pypi_version_sync()

    assert info == {"current": "2.2.7", "latest": "2.2.7", "has_update": False, "error": None}
