"""add inbox query indexes

Revision ID: 20260606_0019
Revises: 20260606_0018
Create Date: 2026-06-06
"""

from __future__ import annotations

from alembic import op

revision = "20260606_0019"
down_revision = "20260606_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "create index if not exists ix_messages_inbox_activity "
        "on messages (platform, session_id, created_at desc, id desc) "
        "where session_id is not null and type not in ('queued', 'draft', 'pending')"
    )
    bind.exec_driver_sql(
        "create index if not exists ix_messages_inbox_agent_reply "
        "on messages (platform, session_id, created_at desc, id desc) "
        "where session_id is not null and type in ('result', 'notify', 'error')"
    )
    bind.exec_driver_sql(
        "create index if not exists ix_messages_inbox_user_send "
        "on messages (platform, session_id, created_at desc, id desc) "
        "where session_id is not null and author = 'user' and type not in ('queued', 'draft', 'pending')"
    )


def downgrade() -> None:
    op.drop_index("ix_messages_inbox_user_send", table_name="messages")
    op.drop_index("ix_messages_inbox_agent_reply", table_name="messages")
    op.drop_index("ix_messages_inbox_activity", table_name="messages")
