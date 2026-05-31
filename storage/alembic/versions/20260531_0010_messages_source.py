"""add messages.source column

Adds a ``source`` column to ``messages`` recording the ORIGIN of each message
(``user`` / ``agent`` / ``harness``), distinct from the coarse ``author`` role:
a Harness-triggered prompt has ``author='user'`` but ``source='harness'``, so
the origin cannot be derived from ``author`` alone. ``author_name`` carries the
display name (username / agent_name / ``task``|``watch``) and ``author_id`` the
precise id (user id / task or watch id / agent id).

Backfills existing rows from ``author`` (user -> user; agent/system -> agent);
``harness`` rows only appear going forward.

Revision ID: 20260531_0010
Revises: 20260531_0009
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260531_0010"
down_revision = "20260531_0009"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {row[1] for row in bind.exec_driver_sql(f'pragma table_info("{table}")')}


def _tables(bind) -> set[str]:
    return {row[0] for row in bind.exec_driver_sql("select name from sqlite_master where type = 'table'")}


def upgrade() -> None:
    bind = op.get_bind()
    if "messages" not in _tables(bind):
        return
    if "source" in _columns(bind, "messages"):
        return

    # Nullable: provenance is best-effort; historical rows are backfilled from
    # ``author`` below, and ``harness`` can only be set on new inserts.
    op.add_column("messages", sa.Column("source", sa.String(), nullable=True))

    bind.exec_driver_sql(
        """
        update messages
        set source = case
            when author = 'user' then 'user'
            when author = 'agent' then 'agent'
            when author = 'system' then 'agent'
            else null
        end
        where source is null
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "messages" in _tables(bind) and "source" in _columns(bind, "messages"):
        op.drop_column("messages", "source")
