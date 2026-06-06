"""add workbench query indexes

Revision ID: 20260606_0018
Revises: 20260604_0017
Create Date: 2026-06-06
"""

from __future__ import annotations

from alembic import op

revision = "20260606_0018"
down_revision = "20260604_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_agent_sessions_scope_status_activity",
        "agent_sessions",
        ["scope_id", "status", "last_active_at", "created_at", "id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_messages_session_created_id",
        "messages",
        ["session_id", "created_at", "id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_messages_session_type_created_id",
        "messages",
        ["session_id", "type", "created_at", "id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_messages_platform_session_created_id",
        "messages",
        ["platform", "session_id", "created_at", "id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_messages_unread_session",
        "messages",
        ["platform", "type", "author", "read_at", "session_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_messages_mark_read",
        "messages",
        ["session_id", "author", "read_at", "created_at", "id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_mark_read", table_name="messages")
    op.drop_index("ix_messages_unread_session", table_name="messages")
    op.drop_index("ix_messages_platform_session_created_id", table_name="messages")
    op.drop_index("ix_messages_session_type_created_id", table_name="messages")
    op.drop_index("ix_messages_session_created_id", table_name="messages")
    op.drop_index("ix_agent_sessions_scope_status_activity", table_name="agent_sessions")
