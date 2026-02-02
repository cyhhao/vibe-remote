from config import paths


def test_paths_are_under_home():
    root = paths.get_vibe_remote_dir()
    assert root.name == ".vibe_remote"
    assert paths.get_config_path().parent == paths.get_config_dir()
    assert paths.get_settings_path().parent == paths.get_state_dir()
    assert paths.get_sessions_path().parent == paths.get_state_dir()


def test_ensure_data_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    paths.ensure_data_dirs()
    assert (tmp_path / ".vibe_remote" / "config").exists()
    assert (tmp_path / ".vibe_remote" / "state").exists()
    assert (tmp_path / ".vibe_remote" / "logs").exists()
    assert (tmp_path / ".vibe_remote" / "runtime").exists()
    assert (tmp_path / ".vibe_remote" / "attachments").exists()
