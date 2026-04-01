import json
from pathlib import Path

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


def test_discovered_chats_save_keeps_target_file_valid_until_replace(tmp_path, monkeypatch):
    storage_path = tmp_path / "state" / "discovered_chats.json"
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platforms": {
                    "telegram": {
                        "-1001": {
                            "chat_id": "-1001",
                            "platform": "telegram",
                            "name": "Old Chat",
                            "username": "",
                            "chat_type": "group",
                            "is_private": False,
                            "is_forum": False,
                            "supports_topics": False,
                            "last_seen_at": "2026-04-01T00:00:00+00:00",
                        }
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "ensure_data_dirs", lambda: None)

    store = DiscoveredChatsStore(storage_path)
    original_replace = Path.replace
    observed_before_replace: dict[str, object] = {}

    def checking_replace(self: Path, target: Path) -> Path:
        if target == storage_path:
            observed_before_replace["payload"] = json.loads(target.read_text(encoding="utf-8"))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", checking_replace)

    store.remember_chat(
        platform="telegram",
        chat_id="-1002",
        name="New Chat",
        chat_type="supergroup",
        is_forum=True,
    )

    before_replace = observed_before_replace["payload"]
    assert before_replace["platforms"]["telegram"]["-1001"]["name"] == "Old Chat"
    assert "-1002" not in before_replace["platforms"]["telegram"]

    saved_payload = json.loads(storage_path.read_text(encoding="utf-8"))
    assert saved_payload["platforms"]["telegram"]["-1002"]["name"] == "New Chat"
