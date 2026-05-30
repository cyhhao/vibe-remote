"""Unit tests for the cross-platform message mirror + unified agent persist.

Covers the contract that ``MessageHandler`` / ``ConsolidatedMessageDispatcher``
rely on:

* a fresh ``(platform, channel_id)`` auto-upserts as a 'channel'-typed scope on
  first inbound mirror, writing an author='user', type='user' row,
* ``persist_agent_message`` lands an author='agent' row (typed) on the same
  scope for the live reply,
* repeated inbound mirror calls with the same ``native_message_id`` are
  idempotent,
* ``mirror_inbound`` is a no-op for ``platform='avibe'`` (the workbench REST
  writer owns the user row), while ``persist_agent_message`` DOES persist avibe
  agent output (unified store).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.message_mirror import mirror_inbound, persist_agent_message
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
        assert message_rows[0]["type"] == "user"
        assert message_rows[0]["content_text"] == "hello there"
        assert message_rows[0]["author_id"] == "U_alice"


def test_persist_agent_writes_typed_agent_row_on_same_scope(isolated_state):
    ctx = _slack_ctx()
    mirror_inbound(ctx, "ping")
    persist_agent_message(ctx, "result", "pong")

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(messages).where(messages.c.platform == "slack")
        ).mappings().all()
    # Two separate-second-resolution writes can tie on created_at, so assert by
    # author rather than row order.
    assert {row["author"] for row in rows} == {"user", "agent"}
    agent_row = next(r for r in rows if r["author"] == "agent")
    user_row = next(r for r in rows if r["author"] == "user")
    assert agent_row["content_text"] == "pong"
    assert agent_row["type"] == "result"
    # No session resolved on this synthetic context -> falls back to the
    # channel scope auto-created on first inbound; both rows share it.
    assert agent_row["scope_id"] == user_row["scope_id"]


def test_persist_agent_maps_canonical_type(isolated_state):
    ctx = _slack_ctx()
    mirror_inbound(ctx, "ping")
    persist_agent_message(ctx, "toolcall", "ran a tool")

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        agent_row = conn.execute(
            select(messages).where(messages.c.author == "agent")
        ).mappings().first()
    # canonical 'toolcall' persists as the 'tool_call' type.
    assert agent_row["type"] == "tool_call"


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


def test_avibe_inbound_is_noop(isolated_state):
    """avibe user messages are written by the workbench REST endpoint, so the
    inbound mirror stays a no-op (agent output is persisted via
    persist_agent_message, which is exercised in the messages_service tests)."""
    avibe_ctx = MessageContext(
        user_id="U_alice",
        channel_id="avibe-channel",
        platform="avibe",
        message_id="avibe_001",
    )
    mirror_inbound(avibe_ctx, "this should not land")

    engine = create_sqlite_engine()
    with engine.connect() as conn:
        rows = conn.execute(select(messages).where(messages.c.author == "user")).mappings().all()
    assert rows == []
