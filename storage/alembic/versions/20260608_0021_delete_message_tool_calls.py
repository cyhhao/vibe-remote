"""delete historical message tool-call rows

Revision ID: 20260608_0021
Revises: 20260608_0020
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import op

revision = "20260608_0021"
down_revision = "20260608_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("delete from messages where type = 'tool_call'")


def downgrade() -> None:
    # Tool-call rows are disposable trace data. This cleanup intentionally does
    # not attempt to reconstruct deleted historical rows.
    return None
