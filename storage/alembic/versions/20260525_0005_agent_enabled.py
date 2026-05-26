"""add enabled state to vibe agents

Revision ID: 20260525_0005
Revises: 20260523_0004
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260525_0005"
down_revision = "20260523_0004"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    return {row[1] for row in bind.exec_driver_sql(f'pragma table_info("{table_name}")')}


def upgrade() -> None:
    if "enabled" not in _columns("agents"):
        op.add_column("agents", sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    pass
