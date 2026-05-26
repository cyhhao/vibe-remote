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
* ``platform='avibe'`` is a no-op so the workbench REST writer stays the
  single source of truth.
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


def test_avibe_platform_is_noop(isolated_state):
    avibe_ctx = MessageContext(
        user_id="U_alice",
        channel_id="avibe-channel",
        platform="avibe",
        message_id="avibe_001",
    )
    mirror_inbound(avibe_ctx, "this should not land")
    mirror_outbound(avibe_ctx, "neither should this", native_message_id="avibe_002")

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        rows = conn.execute(select(messages)).mappings().all()
    assert rows == []
