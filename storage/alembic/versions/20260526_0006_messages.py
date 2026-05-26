"""platform-agnostic messages table

Revision ID: 20260526_0006
Revises: 20260525_0005
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260526_0006"
down_revision = "20260525_0005"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    bind = op.get_bind()
    return {row[0] for row in bind.exec_driver_sql("select name from sqlite_master where type = 'table'")}


def upgrade() -> None:
    if "messages" not in _tables():
        op.create_table(
            "messages",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "scope_id",
                sa.String(),
                sa.ForeignKey("scopes.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "session_id",
                sa.String(),
                sa.ForeignKey("agent_sessions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("platform", sa.String(), nullable=False),
            sa.Column("author", sa.String(), nullable=False),
            sa.Column("author_id", sa.String(), nullable=True),
            sa.Column("author_name", sa.Text(), nullable=True),
            sa.Column("native_message_id", sa.String(), nullable=True),
            sa.Column("parent_native_message_id", sa.String(), nullable=True),
            sa.Column("content_text", sa.Text(), nullable=True),
            sa.Column("content_json", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
            sa.Column("delivered_at", sa.String(), nullable=True),
            sa.Column("read_at", sa.String(), nullable=True),
            sa.UniqueConstraint("platform", "native_message_id", name="uq_messages_platform_native"),
        )
    op.create_index(
        "ix_messages_session_created",
        "messages",
        ["session_id", "created_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_messages_scope_created",
        "messages",
        ["scope_id", "created_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_messages_scope_unread",
        "messages",
        ["scope_id", "read_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_messages_author_created",
        "messages",
        ["author", "created_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("messages")
