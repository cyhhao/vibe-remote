from config import paths
from config.discovered_chats import DiscoveredChatsStore


def test_discovered_chats_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    DiscoveredChatsStore.reset_instance()
    store = DiscoveredChatsStore.get_instance()
    store.remember_chat(
        platform="telegram",
        chat_id="-1001",
        name="Core Forum",
        chat_type="supergroup",
        is_forum=True,
        supports_topics=True,
    )

    DiscoveredChatsStore.reset_instance()
    reloaded = DiscoveredChatsStore.get_instance()
    chats = reloaded.list_chats("telegram", include_private=False)

    assert len(chats) == 1
    assert chats[0].name == "Core Forum"
    assert chats[0].supports_topics is True
    DiscoveredChatsStore.reset_instance()


def test_discovered_chats_store_merges_forum_capability(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    DiscoveredChatsStore.reset_instance()
    store = DiscoveredChatsStore.get_instance()
    store.remember_chat(platform="telegram", chat_id="-1001", name="Core Group", chat_type="supergroup")
    store.remember_chat(platform="telegram", chat_id="-1001", name="Core Group", chat_type="supergroup", is_forum=True)

    chats = store.list_chats("telegram", include_private=False)

    assert len(chats) == 1
    assert chats[0].is_forum is True
    DiscoveredChatsStore.reset_instance()
