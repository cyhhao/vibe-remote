from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_settings import SettingsStore, UserSettings
from config.v2_config import UpdateConfig
from core import update_checker
from core.update_checker import UpdateChecker


RELEASE_URL_101 = "https://github.com/cyhhao/vibe-remote/releases/tag/v1.0.1"


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
        self.im_clients = {}


class _FakeIMClient:
    def __init__(self, message_id="msg-1"):
        self.message_id = message_id
        self.dm_calls = []

    async def send_dm(self, user_id: str, text: str, **kwargs):
        self.dm_calls.append((user_id, text, kwargs))
        return self.message_id


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


def test_update_notification_admin_dms_include_buttons_except_wechat(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()
    store = SettingsStore.get_instance()
    store.set_users_for_platform("slack", {"U1": UserSettings(display_name="Slack", is_admin=True)})
    store.set_users_for_platform("discord", {"123456789012345678": UserSettings(display_name="Discord", is_admin=True)})
    store.set_users_for_platform("telegram", {"123456": UserSettings(display_name="Telegram", is_admin=True)})
    store.set_users_for_platform("lark", {"ou_admin": UserSettings(display_name="Lark", is_admin=True)})
    store.set_users_for_platform("wechat", {"wx_admin": UserSettings(display_name="WeChat", is_admin=True)})
    store.save()

    controller = _StubController(store)
    clients = {platform: _FakeIMClient() for platform in ["slack", "discord", "telegram", "lark", "wechat"]}
    controller.im_clients = clients
    controller.im_client = clients["slack"]
    checker = UpdateChecker(controller, UpdateConfig())

    delivered = asyncio.run(checker._send_update_notification("1.0.0", "1.0.1"))

    assert delivered is True
    slack_kwargs = clients["slack"].dm_calls[0][2]
    assert slack_kwargs["blocks"][1]["elements"][0]["action_id"] == "vibe_update_now"
    assert slack_kwargs["blocks"][1]["elements"][0]["value"] == "1.0.1"
    assert f"<{RELEASE_URL_101}|1.0.1>" in slack_kwargs["blocks"][0]["text"]["text"]
    assert RELEASE_URL_101 in clients["slack"].dm_calls[0][1]

    for platform in ["discord", "telegram", "lark"]:
        text = clients[platform].dm_calls[0][1]
        assert f"[1.0.1]({RELEASE_URL_101})" in text
        kwargs = clients[platform].dm_calls[0][2]
        keyboard = kwargs["keyboard"]
        assert keyboard.buttons[0][0].text == "Update Now"
        assert keyboard.buttons[0][0].callback_data == "vibe_update_now:1.0.1"

    assert f"1.0.1 ({RELEASE_URL_101})" in clients["wechat"].dm_calls[0][1]
    assert "keyboard" not in clients["wechat"].dm_calls[0][2]


def test_update_notification_release_url_normalizes_github_tags():
    assert update_checker._github_release_url("1.0.1") == RELEASE_URL_101
    assert update_checker._github_release_url("v1.0.1") == RELEASE_URL_101
    assert (
        update_checker._github_release_url("gh-v2.2.8rc1")
        == "https://github.com/cyhhao/vibe-remote/releases/tag/gh-v2.2.8rc1"
    )


def test_update_notification_returns_false_when_all_admin_dms_fail(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()
    store = SettingsStore.get_instance()
    store.set_users_for_platform("discord", {"123456789012345678": UserSettings(display_name="Discord", is_admin=True)})
    store.set_users_for_platform("telegram", {"123456": UserSettings(display_name="Telegram", is_admin=True)})
    store.save()

    controller = _StubController(store)
    controller.im_clients = {
        "discord": _FakeIMClient(message_id=None),
        "telegram": _FakeIMClient(message_id=None),
    }
    checker = UpdateChecker(controller, UpdateConfig())

    delivered = asyncio.run(checker._send_update_notification("1.0.0", "1.0.1"))

    assert delivered is False


def test_failed_update_notification_does_not_defer_idle_auto_update(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()
    store = SettingsStore.get_instance()
    store.set_users_for_platform("discord", {"123456789012345678": UserSettings(display_name="Discord", is_admin=True)})
    store.save()

    controller = _StubController(store)
    controller.im_clients = {"discord": _FakeIMClient(message_id=None)}
    checker = UpdateChecker(controller, UpdateConfig(check_interval_minutes=1, notify_admins=True, auto_update=True))
    checker.state.last_activity_at = time.time() - 3600
    monkeypatch.setattr(
        update_checker,
        "_fetch_pypi_version_sync",
        lambda: {"current": "1.0.0", "latest": "1.0.1", "has_update": True, "error": None},
    )
    monkeypatch.setattr(checker, "_is_idle", lambda: True)
    performed = []

    async def fake_perform_update(target_version, **kwargs):
        performed.append((target_version, kwargs))
        return {"ok": True, "restarting": False, "message": "ok"}

    monkeypatch.setattr(checker, "_perform_update", fake_perform_update)

    asyncio.run(checker._do_check())

    assert checker.state.notified_version is None
    assert checker.state.notified_at is None
    assert performed == [("1.0.1", {})]


def test_update_marker_records_platform_for_non_slack_callbacks(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()
    checker = UpdateChecker(_StubController(SettingsStore.get_instance()), UpdateConfig())

    checker._write_update_marker("1.0.1", channel_id="123456", message_id="42", platform="telegram")

    marker = tmp_path / "state" / "pending_update_notification.json"
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["platform"] == "telegram"
    assert data["channel_id"] == "123456"
    assert data["message_id"] == "42"


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
