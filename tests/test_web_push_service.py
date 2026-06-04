from __future__ import annotations

import json

import pytest

from core.web_push import DEFAULT_WEB_PUSH_TIMEOUT_SECONDS, load_or_create_vapid_keys, send_web_push
from storage import web_push_service
from storage.db import create_sqlite_engine
from storage.migrations import run_migrations


def _payload(endpoint: str = "https://push.example.test/sub/1") -> dict:
    return {
        "endpoint": endpoint,
        "keys": {
            "p256dh": "p256dh-key",
            "auth": "auth-secret",
        },
    }


def test_subscription_upsert_and_disable(tmp_path):
    db = tmp_path / "vibe.sqlite"
    run_migrations(db)
    engine = create_sqlite_engine(db)

    with engine.begin() as conn:
        row = web_push_service.upsert_subscription(conn, user_key="local", payload=_payload(), user_agent="ua1")
        assert row["enabled"] is True
        assert row["user_agent"] == "ua1"
        assert web_push_service.count_enabled(conn, user_key="local") == 1

        updated = web_push_service.upsert_subscription(conn, user_key="local", payload=_payload(), user_agent="ua2")
        assert updated["id"] == row["id"]
        assert updated["user_agent"] == "ua2"
        assert web_push_service.count_enabled(conn, user_key="local") == 1

        assert web_push_service.disable_subscription(
            conn,
            endpoint=_payload()["endpoint"],
            user_key="someone-else",
        ) is False
        assert web_push_service.count_enabled(conn, user_key="local") == 1

        assert web_push_service.disable_subscription(conn, endpoint=_payload()["endpoint"], user_key="local") is True
        assert web_push_service.count_enabled(conn, user_key="local") == 0


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({}, "endpoint_required"),
        ({"endpoint": "http://example.test", "keys": {"p256dh": "x", "auth": "y"}}, "endpoint_must_be_https"),
        ({"endpoint": "https://example.test", "keys": {}}, "p256dh_required"),
        ({"endpoint": "https://example.test", "keys": {"p256dh": "x"}}, "auth_required"),
    ],
)
def test_validate_subscription_payload_rejects_bad_input(payload, error):
    with pytest.raises(ValueError, match=error):
        web_push_service.validate_subscription_payload(payload)


def test_vapid_keys_are_stable(tmp_path):
    key_path = tmp_path / "web_push_vapid.json"

    first = load_or_create_vapid_keys(key_path)
    second = load_or_create_vapid_keys(key_path)

    assert first == second
    assert first.public_key
    assert "PRIVATE KEY" in first.private_key_pem
    stored = json.loads(key_path.read_text(encoding="utf-8"))
    assert stored["public_key"] == first.public_key


def test_send_web_push_passes_vapid_signer_and_timeout(monkeypatch, tmp_path):
    keys = load_or_create_vapid_keys(tmp_path / "web_push_vapid.json")
    calls = []

    def fake_webpush(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("pywebpush.webpush", fake_webpush)

    send_web_push(
        subscription={
            "endpoint": "https://push.example.test/sub/1",
            "p256dh": "p256dh-key",
            "auth": "auth-secret",
        },
        payload={"title": "Hello"},
        vapid_keys=keys,
    )

    assert len(calls) == 1
    assert calls[0]["timeout"] == DEFAULT_WEB_PUSH_TIMEOUT_SECONDS
    assert not isinstance(calls[0]["vapid_private_key"], str)
    assert calls[0]["subscription_info"]["endpoint"] == "https://push.example.test/sub/1"
