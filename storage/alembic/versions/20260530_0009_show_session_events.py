"""show session events

Revision ID: 20260530_0009
Revises: 20260530_0008
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260530_0009"
down_revision = "20260530_0008"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    bind = op.get_bind()
    return {row[0] for row in bind.exec_driver_sql("select name from sqlite_master where type = 'table'")}


def upgrade() -> None:
    if "show_session_events" not in _tables():
        op.create_table(
            "show_session_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("scope", sa.String(), nullable=False),
            sa.Column("anchor_json", sa.Text(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("transcript_text", sa.Text(), nullable=True),
            sa.Column("message_id", sa.String(), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
        )
    op.create_index(
        "ix_show_session_events_session_created",
        "show_session_events",
        ["session_id", "created_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_show_session_events_type_created",
        "show_session_events",
        ["event_type", "created_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("show_session_events")
