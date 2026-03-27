from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_settings import SettingsStore, UserSettings
from config.v2_config import UpdateConfig
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
