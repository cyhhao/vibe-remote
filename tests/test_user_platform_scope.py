from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_settings import SettingsStore, UserSettings
from vibe import api


def test_get_users_respects_platform_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()
    store = SettingsStore.get_instance()
    store.set_users_for_platform(
        "slack",
        {"U1": UserSettings(display_name="Slack Admin", is_admin=True)},
    )
    store.set_users_for_platform(
        "wechat",
        {"wx1": UserSettings(display_name="WeChat Admin", is_admin=True)},
    )
    store.save()

    slack_users = api.get_users("slack")
    wechat_users = api.get_users("wechat")

    assert set(slack_users["users"].keys()) == {"U1"}
    assert set(wechat_users["users"].keys()) == {"wx1"}


def test_toggle_admin_is_scoped_per_platform(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    SettingsStore.reset_instance()
    store = SettingsStore.get_instance()
    store.set_users_for_platform(
        "slack",
        {"U1": UserSettings(display_name="Slack User", is_admin=False)},
    )
    store.set_users_for_platform(
        "wechat",
        {"U1": UserSettings(display_name="WeChat User", is_admin=False)},
    )
    store.save()

    result = api.toggle_admin("U1", True, "wechat")

    assert result["ok"] is True
    assert store.get_user("U1", platform="slack").is_admin is False
    assert store.get_user("U1", platform="wechat").is_admin is True
