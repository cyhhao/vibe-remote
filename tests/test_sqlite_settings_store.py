from __future__ import annotations

import json
from pathlib import Path

from config import paths
from config.v2_settings import ChannelSettings, SettingsState, SettingsStore, UserSettings
from storage.sessions_service import SQLiteSessionsService
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


def test_settings_store_bootstrap_uses_config_primary_platform(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    paths.get_config_path().write_text(
        json.dumps({"platform": "discord", "platforms": {"enabled": ["discord"], "primary": "discord"}}),
        encoding="utf-8",
    )
    sessions_path = paths.get_sessions_path()
    sessions_path.write_text(
        json.dumps(
            {
                "session_mappings": {"G123": {"codex": {"1774074591.762089:/repo": "session-1"}}},
                "active_polls": {
                    "oc-1": {
                        "opencode_session_id": "oc-1",
                        "base_session_id": "base-1",
                        "channel_id": "G123",
                        "thread_id": "1774074591.762089",
                        "settings_key": "G123",
                        "working_path": "/repo",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store = SettingsStore(paths.get_settings_path())
    sessions = SQLiteSessionsService(paths.get_sqlite_state_path())
    try:
        state = sessions.load_state()
        assert "discord::G123" in state.session_mappings
        assert state.active_polls["oc-1"]["platform"] == "discord"
    finally:
        sessions.close()
        store.close()


def test_settings_store_custom_path_uses_sibling_config_primary_platform(tmp_path: Path) -> None:
    root = tmp_path / "custom-home"
    state_dir = root / "state"
    config_dir = root / "config"
    state_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"platform": "discord", "platforms": {"enabled": ["discord"], "primary": "discord"}}),
        encoding="utf-8",
    )
    (state_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session_mappings": {"G456": {"codex": {"1774074591.762089:/repo": "session-1"}}},
                "active_polls": {
                    "oc-2": {
                        "opencode_session_id": "oc-2",
                        "base_session_id": "base-2",
                        "channel_id": "G456",
                        "thread_id": "1774074591.762089",
                        "settings_key": "G456",
                        "working_path": "/repo",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store = SettingsStore(state_dir / "settings.json")
    sessions = SQLiteSessionsService(state_dir / "vibe.sqlite")
    try:
        state = sessions.load_state()
        assert "discord::G456" in state.session_mappings
        assert state.active_polls["oc-2"]["platform"] == "discord"
    finally:
        sessions.close()
        store.close()
