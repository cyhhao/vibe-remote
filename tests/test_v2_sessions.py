from config import paths
from config.v2_sessions import SessionsStore


def test_sessions_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    store = SessionsStore()
    store.state.session_mappings = {
        "U1": {"claude": {"base": {"/tmp": "session-1"}}}
    }
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
