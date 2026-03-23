from config import paths
from config.v2_sessions import SessionsStore


def test_sessions_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    store = SessionsStore()
    store.state.session_mappings = {"U1": {"claude": {"base": {"/tmp": "session-1"}}}}
    store.state.active_slack_threads = {"U1": {"C1": {"123.456": 1.0}}}
    store.state.last_activity = "2026-01-18T12:00:00Z"
    store.save()

    reloaded = SessionsStore()
    reloaded.load()
    assert reloaded.state.session_mappings["U1"]["claude"]["base"]["/tmp"] == "session-1"
    assert reloaded.state.active_slack_threads["U1"]["C1"]["123.456"] == 1.0
    assert reloaded.state.last_activity == "2026-01-18T12:00:00Z"


def test_sessions_store_namespaces(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    store = SessionsStore()
    agent_map = store.get_agent_map("U2", "opencode")
    thread_map = store.get_thread_map("U2", "C2")
    assert agent_map == {}
    assert thread_map == {}
    assert "U2" in store.state.session_mappings
    assert "U2" in store.state.active_slack_threads


def test_migrate_active_polls_backfills_platform_and_scoped_key(tmp_path, monkeypatch):
    """Legacy active_polls lacking platform / scoped settings_key are migrated."""
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    store = SessionsStore()
    # Simulate a legacy poll saved before the multi-platform PR
    store.state.active_polls = {
        "oc-session-1": {
            "opencode_session_id": "oc-session-1",
            "base_session_id": "C123:msg1",
            "channel_id": "C123",
            "thread_id": "t1",
            "settings_key": "C123",  # old unscoped key
            "working_path": "/tmp/work",
            "platform": "",  # missing platform
            "user_id": "U1",
        }
    }
    store.save()

    reloaded = SessionsStore()
    reloaded.load()
    reloaded.migrate_active_polls("slack")

    poll = reloaded.state.active_polls["oc-session-1"]
    assert poll["platform"] == "slack"
    assert poll["settings_key"] == "slack::C123"


def test_migrate_active_polls_skips_already_scoped(tmp_path, monkeypatch):
    """Already-scoped polls are not double-prefixed."""
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    store = SessionsStore()
    store.state.active_polls = {
        "oc-session-2": {
            "opencode_session_id": "oc-session-2",
            "base_session_id": "C456:msg2",
            "channel_id": "C456",
            "thread_id": "t2",
            "settings_key": "discord::C456",
            "working_path": "/tmp/work",
            "platform": "discord",
            "user_id": "U2",
        }
    }
    store.save()

    reloaded = SessionsStore()
    reloaded.load()
    reloaded.migrate_active_polls("slack")

    poll = reloaded.state.active_polls["oc-session-2"]
    assert poll["platform"] == "discord"
    assert poll["settings_key"] == "discord::C456"
