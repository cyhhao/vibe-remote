from config import paths


def test_paths_are_under_home():
    root = paths.get_vibe_remote_dir()
    assert root.name == ".vibe_remote"
    assert paths.get_config_path().parent == paths.get_config_dir()
    assert paths.get_settings_path().parent == paths.get_state_dir()
    assert paths.get_sessions_path().parent == paths.get_state_dir()
    assert paths.get_user_preferences_path().parent == paths.get_state_dir()


def test_ensure_data_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    paths.ensure_data_dirs()
    assert (tmp_path / ".vibe_remote" / "config").exists()
    assert (tmp_path / ".vibe_remote" / "state").exists()
    assert (tmp_path / ".vibe_remote" / "logs").exists()
    assert (tmp_path / ".vibe_remote" / "runtime").exists()
    assert (tmp_path / ".vibe_remote" / "attachments").exists()
    preferences_path = tmp_path / ".vibe_remote" / "state" / "user_preferences.md"
    assert preferences_path.exists()
    text = preferences_path.read_text(encoding="utf-8")
    assert "# User Context and Preferences" in text
    assert "Prefer adding notes under `## Users`." in text
    assert "### platform/user_id" in text
    assert "communicate, work, and make decisions." in text
    assert "free of secrets unless the user explicitly asks." in text
