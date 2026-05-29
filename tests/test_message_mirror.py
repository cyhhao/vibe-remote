"""Unit tests for the cross-platform message mirror.

These cover the contract that
``core.handlers.message_handler.MessageHandler`` and
``core.message_dispatcher.ConsolidatedMessageDispatcher`` rely on:

* a fresh ``(platform, channel_id)`` is auto-upserted as a 'channel'-typed
  scope on first inbound mirror,
* the same call also writes an author='user' row,
* a follow-up outbound mirror lands an author='agent' row on the same
  scope,
* repeated mirror calls with the same ``native_message_id`` are
  idempotent (no extra rows, no raised exception),
* ``platform='avibe'`` inbound is a no-op (the workbench REST writer owns
  the user row), while avibe *outbound* replies are persisted under the
  originating session so the Chat transcript shows the agent's answer once
  the live stream settles.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.message_mirror import mirror_inbound, mirror_outbound
from modules.im import MessageContext
from storage.db import create_sqlite_engine
from storage.importer import ensure_sqlite_state
from storage.models import messages, scopes


@pytest.fixture()
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    yield tmp_path


def _slack_ctx(message_id="m_001") -> MessageContext:
    return MessageContext(
        user_id="U_alice",
        channel_id="C_general",
        platform="slack",
        thread_id=None,
        message_id=message_id,
    )


def test_inbound_creates_scope_and_user_row(isolated_state):
    mirror_inbound(_slack_ctx(), "hello there")

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        scope_row = conn.execute(
            select(scopes).where(scopes.c.platform == "slack", scopes.c.native_id == "C_general")
        ).mappings().first()
        assert scope_row is not None
        assert scope_row["scope_type"] == "channel"

        message_rows = conn.execute(
            select(messages).where(messages.c.platform == "slack")
        ).mappings().all()
        assert len(message_rows) == 1
        assert message_rows[0]["author"] == "user"
        assert message_rows[0]["content_text"] == "hello there"
        assert message_rows[0]["author_id"] == "U_alice"


def test_outbound_writes_agent_row_on_same_scope(isolated_state):
    ctx = _slack_ctx()
    mirror_inbound(ctx, "ping")
    mirror_outbound(ctx, "pong", native_message_id="slack_m_002", kind="result")

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(messages).where(messages.c.platform == "slack").order_by(messages.c.created_at)
        ).mappings().all()
        assert [row["author"] for row in rows] == ["user", "agent"]
        assert rows[1]["content_text"] == "pong"
        assert rows[1]["native_message_id"] == "slack_m_002"
        # Both rows share the scope auto-created on first inbound.
        assert rows[0]["scope_id"] == rows[1]["scope_id"]


def test_duplicate_native_message_id_is_swallowed(isolated_state):
    ctx = _slack_ctx(message_id="dup_id")
    mirror_inbound(ctx, "first")
    mirror_inbound(ctx, "duplicate delivery")

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        rows = conn.execute(select(messages).where(messages.c.platform == "slack")).mappings().all()
    # Unique (platform, native_message_id) constraint keeps the second
    # write from materializing.
    assert len(rows) == 1
    assert rows[0]["content_text"] == "first"


def test_avibe_inbound_noop_and_unknown_session_outbound_skipped(isolated_state):
    # avibe inbound is always a no-op (ui_server's REST writer owns the user
    # row). avibe outbound only persists when it resolves a real session — an
    # unknown session id must NOT auto-create scopes or rows.
    avibe_ctx = MessageContext(
        user_id="U_alice",
        channel_id="avibe-channel",
        platform="avibe",
        message_id="avibe_001",
    )
    mirror_inbound(avibe_ctx, "this should not land")
    mirror_outbound(avibe_ctx, "no matching session", native_message_id="avibe_002")

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        rows = conn.execute(select(messages)).mappings().all()
    assert rows == []


def _make_avibe_session() -> tuple[str, str]:
    """Create a real avibe project scope + session row.

    Mirrors ``tests/test_ui_session_stream.py::_make_session``. Returns
    ``(scope_id, session_id)``.
    """

    from core.services import sessions as sessions_service
    from storage.settings_service import upsert_scope

    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = upsert_scope(
            conn,
            platform="avibe",
            scope_type="project",
            native_id="proj_mirror",
            now="2026-05-30T00:00:00Z",
        )
        session = sessions_service.create_session(
            conn,
            scope_id=scope_id,
            agent_backend="claude",
            agent_name="worker",
        )
    return scope_id, session["id"]


def test_avibe_outbound_persists_reply_under_session(isolated_state):
    # Regression for the workbench "sent a message, got no response" bug
    # (#7): avibe agent replies must land in the messages table under the
    # originating session, because the live SSE stream is ephemeral and the
    # Chat page's post-stream refresh re-reads the persisted row. The session
    # id rides on ``platform_specific["workbench_session_id"]``.
    scope_id, session_id = _make_avibe_session()
    ctx = MessageContext(
        user_id="workbench",
        channel_id=session_id,
        platform="avibe",
        message_id=None,
        platform_specific={"workbench_session_id": session_id},
    )
    mirror_outbound(ctx, "the agent reply", native_message_id="avibe_reply_1", kind="result")

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(messages).where(messages.c.platform == "avibe")
        ).mappings().all()
    assert len(rows) == 1
    assert rows[0]["author"] == "agent"
    assert rows[0]["content_text"] == "the agent reply"
    assert rows[0]["session_id"] == session_id
    assert rows[0]["scope_id"] == scope_id
    assert rows[0]["native_message_id"] == "avibe_reply_1"


def test_outbound_mirror_uses_delivery_target_scope(isolated_state):
    """Regression: dispatcher calls mirror_outbound with ``target_context``,
    which may carry a different channel/platform than the inbound ``context``
    (e.g. ``post_to`` overrides on scheduled / watch-driven runs). The mirror
    must land under the delivery scope, not the source scope.
    """
    delivery_ctx = MessageContext(
        user_id="agent",
        channel_id="C_delivery",  # different from any inbound channel
        platform="slack",
        thread_id=None,
        message_id=None,
    )
    mirror_outbound(delivery_ctx, "agent reply", native_message_id="slack_out_42", kind="result")

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        scope_row = conn.execute(
            select(scopes).where(scopes.c.platform == "slack", scopes.c.native_id == "C_delivery")
        ).mappings().first()
        assert scope_row is not None, "outbound scope must be auto-upserted under the delivery target"
        rows = conn.execute(select(messages).where(messages.c.platform == "slack")).mappings().all()
    assert len(rows) == 1
    assert rows[0]["author"] == "agent"
    assert rows[0]["content_text"] == "agent reply"
    assert rows[0]["scope_id"] == scope_row["id"]
