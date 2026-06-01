"""add agent_sessions.agent_status column

Adds a live agent-runtime ``agent_status`` to ``agent_sessions``, distinct from
the lifecycle ``status`` (active/archived). One of ``idle`` / ``running`` /
``failed``: ``running`` while a turn is in flight, ``failed`` when the most
recent turn ended in error, ``idle`` otherwise. Drives the workbench sidebar
status dot (gray / green / red).

Existing rows default to ``idle`` (the column is NOT NULL with a server default,
so the backfill is implicit). A stale ``running`` left by a crash is reset to
``idle`` on controller startup — no turn survives a restart.

Revision ID: 20260601_0012
Revises: 20260601_0011
Create Date: 2026-06-01

(Rebased onto the (scope, anchor) migration 20260601_0011 when the two feature
branches merged — both originally branched from 20260531_0010, producing two
alembic heads. The two changes are independent; this only linearizes them.)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260601_0012"
down_revision = "20260601_0011"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {row[1] for row in bind.exec_driver_sql(f'pragma table_info("{table}")')}


def _tables(bind) -> set[str]:
    return {row[0] for row in bind.exec_driver_sql("select name from sqlite_master where type = 'table'")}


def upgrade() -> None:
    bind = op.get_bind()
    if "agent_sessions" not in _tables(bind):
        return
    if "agent_status" in _columns(bind, "agent_sessions"):
        return

    # NOT NULL with a server default so existing rows backfill to ``idle``
    # without a separate update.
    op.add_column(
        "agent_sessions",
        sa.Column("agent_status", sa.String(), nullable=False, server_default="idle"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "agent_sessions" in _tables(bind) and "agent_status" in _columns(bind, "agent_sessions"):
        op.drop_column("agent_sessions", "agent_status")
