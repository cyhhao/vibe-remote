"""add agent events trace table

Revision ID: 20260608_0020
Revises: 20260606_0019
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260608_0020"
down_revision = "20260606_0019"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    bind = op.get_bind()
    return {row[0] for row in bind.exec_driver_sql("select name from sqlite_master where type = 'table'")}


def upgrade() -> None:
    if "agent_events" not in _tables():
        op.create_table(
            "agent_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("scope_id", sa.String(), sa.ForeignKey("scopes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("session_id", sa.String(), sa.ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("turn_id", sa.String(), nullable=True),
            sa.Column("run_id", sa.String(), nullable=True),
            sa.Column("platform", sa.String(), nullable=False),
            sa.Column("agent_name", sa.String(), nullable=True),
            sa.Column("backend", sa.String(), nullable=True),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("visibility", sa.String(), nullable=False, server_default="trace"),
            sa.Column("sequence", sa.Integer(), nullable=True),
            sa.Column("content_text", sa.Text(), nullable=True),
            sa.Column("content_json", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("source", sa.String(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
        )
    op.create_index(
        "ix_agent_events_session_created_id",
        "agent_events",
        ["session_id", "created_at", "id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_agent_events_session_type_created_id",
        "agent_events",
        ["session_id", "event_type", "created_at", "id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_agent_events_scope_created_id",
        "agent_events",
        ["scope_id", "created_at", "id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_agent_events_turn_sequence_id",
        "agent_events",
        ["turn_id", "sequence", "id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_events_turn_sequence_id", table_name="agent_events")
    op.drop_index("ix_agent_events_scope_created_id", table_name="agent_events")
    op.drop_index("ix_agent_events_session_type_created_id", table_name="agent_events")
    op.drop_index("ix_agent_events_session_created_id", table_name="agent_events")
    op.drop_table("agent_events")
