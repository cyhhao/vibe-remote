from __future__ import annotations

from core import web_push_notifications
from storage.importer import ensure_sqlite_state
from storage import web_push_service
from storage.db import create_sqlite_engine
from storage.models import agent_sessions
from storage.settings_service import upsert_scope


def test_maybe_notify_inbox_message_schedules_agent_result(monkeypatch):
    calls = []

    class _Thread:
        def __init__(self, *, target, args, daemon):
            assert daemon is True
            self.target = target
            self.args = args

        def start(self):
            calls.append(self.args[0])

    monkeypatch.setattr(web_push_notifications.threading, "Thread", _Thread)

    web_push_notifications.maybe_notify_inbox_message(
        {
            "id": "msg_1",
            "platform": "avibe",
            "author": "agent",
            "type": "result",
            "session_id": "ses_1",
            "text": "Done",
        },
        {
            "title": "Build fix",
            "project_name": "Vibe Remote",
            "preview_text": "Done",
            "unread_count": 2,
        },
    )

    assert calls == [
        {
            "title": "Build fix",
            "body": "Done",
            "url": "/chat/ses_1",
            "tag": "session:ses_1",
            "badge_count": 2,
            "message_id": "msg_1",
            "session_id": "ses_1",
        }
    ]


def test_maybe_notify_inbox_message_skips_non_notifiable(monkeypatch):
    calls = []
    monkeypatch.setattr(
        web_push_notifications.threading,
        "Thread",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    web_push_notifications.maybe_notify_inbox_message(
        {
            "id": "msg_1",
            "platform": "avibe",
            "author": "agent",
            "type": "assistant",
            "session_id": "ses_1",
            "text": "thinking",
        },
        {"title": "Build fix"},
    )

    assert calls == []


def test_send_to_enabled_subscriptions_uses_session_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    engine = create_sqlite_engine()
    now = "2026-06-04T00:00:00Z"
    with engine.begin() as conn:
        scope_id = upsert_scope(conn, platform="avibe", scope_type="project", native_id="proj_x", now=now)
        conn.execute(
            agent_sessions.insert().values(
                id="ses_owner",
                scope_id=scope_id,
                agent_backend="claude",
                agent_variant="default",
                session_anchor="ses_owner",
                native_session_id="",
                title="Owner",
                status="active",
                metadata_json='{"_web_push_user_key":"remote:user-a"}',
                created_at=now,
                updated_at=now,
                last_active_at=now,
            )
        )
        web_push_service.upsert_subscription(
            conn,
            user_key="remote:user-a",
            payload={
                "endpoint": "https://push.example.test/a",
                "keys": {"p256dh": "a-key", "auth": "a-auth"},
            },
        )
        web_push_service.upsert_subscription(
            conn,
            user_key="remote:user-b",
            payload={
                "endpoint": "https://push.example.test/b",
                "keys": {"p256dh": "b-key", "auth": "b-auth"},
            },
        )

    sends = []
    monkeypatch.setattr(
        "core.web_push.send_web_push",
        lambda *, subscription, payload: sends.append((subscription, payload)),
    )

    web_push_notifications._send_to_enabled_subscriptions(
        {"title": "Owner", "body": "Done", "session_id": "ses_owner"}
    )

    assert [send[0]["endpoint"] for send in sends] == ["https://push.example.test/a"]


def test_send_to_enabled_subscriptions_uses_local_fallback_for_legacy_local_session(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    engine = create_sqlite_engine()
    now = "2026-06-04T00:00:00Z"
    with engine.begin() as conn:
        scope_id = upsert_scope(conn, platform="avibe", scope_type="project", native_id="proj_x", now=now)
        conn.execute(
            agent_sessions.insert().values(
                id="ses_legacy",
                scope_id=scope_id,
                agent_backend="claude",
                agent_variant="default",
                session_anchor="ses_legacy",
                native_session_id="",
                title="Legacy",
                status="active",
                metadata_json="{}",
                created_at=now,
                updated_at=now,
                last_active_at=now,
            )
        )
        web_push_service.upsert_subscription(
            conn,
            user_key="local",
            payload={
                "endpoint": "https://push.example.test/local",
                "keys": {"p256dh": "local-key", "auth": "local-auth"},
            },
        )

    sends = []
    monkeypatch.setattr(
        "core.web_push.send_web_push",
        lambda *, subscription, payload: sends.append((subscription, payload)),
    )
    monkeypatch.setattr(web_push_notifications, "_local_fallback_user_key", lambda: "local")

    web_push_notifications._send_to_enabled_subscriptions(
        {"title": "Legacy", "body": "Done", "session_id": "ses_legacy"}
    )

    assert [send[0]["endpoint"] for send in sends] == ["https://push.example.test/local"]


def test_send_to_enabled_subscriptions_skips_unknown_owner_when_remote_access_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    engine = create_sqlite_engine()
    now = "2026-06-04T00:00:00Z"
    with engine.begin() as conn:
        scope_id = upsert_scope(conn, platform="avibe", scope_type="project", native_id="proj_x", now=now)
        conn.execute(
            agent_sessions.insert().values(
                id="ses_legacy",
                scope_id=scope_id,
                agent_backend="claude",
                agent_variant="default",
                session_anchor="ses_legacy",
                native_session_id="",
                title="Legacy",
                status="active",
                metadata_json="{}",
                created_at=now,
                updated_at=now,
                last_active_at=now,
            )
        )
        web_push_service.upsert_subscription(
            conn,
            user_key="local",
            payload={
                "endpoint": "https://push.example.test/local",
                "keys": {"p256dh": "local-key", "auth": "local-auth"},
            },
        )

    sends = []
    monkeypatch.setattr(
        "core.web_push.send_web_push",
        lambda *, subscription, payload: sends.append((subscription, payload)),
    )
    monkeypatch.setattr(web_push_notifications, "_local_fallback_user_key", lambda: None)

    web_push_notifications._send_to_enabled_subscriptions(
        {"title": "Legacy", "body": "Done", "session_id": "ses_legacy"}
    )

    assert sends == []
