from __future__ import annotations

import json
from pathlib import Path

from config.v2_settings import ChannelSettings, SettingsState, SettingsStore, UserSettings
from storage.settings_service import SQLiteSettingsService


def test_settings_store_uses_sqlite_without_rewriting_legacy_json(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    original = json.dumps(
        {
            "channels": {
                "C123": {
                    "enabled": True,
                    "show_message_types": ["assistant"],
                }
            }
        },
        indent=2,
    )
    settings_path.write_text(original, encoding="utf-8")

    store = SettingsStore(settings_path)
    store.update_channel("C999", ChannelSettings(enabled=True), platform="slack")
    store.close()

    reloaded = SettingsStore(settings_path)
    try:
        assert reloaded.find_channel("C123", platform="slack") is not None
        assert reloaded.find_channel("C999", platform="slack") is not None
        assert settings_path.read_text(encoding="utf-8") == original
    finally:
        reloaded.close()


def test_settings_store_reloads_external_sqlite_writes(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    store = SettingsStore(settings_path)
    external = SQLiteSettingsService(tmp_path / "vibe.sqlite")
    try:
        assert store.get_user("U1", platform="slack") is None

        external.save_state(
            SettingsState(
                users={
                    "slack::U1": UserSettings(display_name="Alex", is_admin=True),
                }
            )
        )

        store.maybe_reload()

        user = store.get_user("U1", platform="slack")
        assert user is not None
        assert user.display_name == "Alex"
        assert user.is_admin is True
    finally:
        external.close()
        store.close()
