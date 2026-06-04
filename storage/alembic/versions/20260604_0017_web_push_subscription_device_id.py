"""add device_id to Web Push subscriptions

Revision ID: 20260604_0017
Revises: 20260604_0016
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260604_0017"
down_revision = "20260604_0016"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {row[1] for row in bind.exec_driver_sql(f'pragma table_info("{table}")')}


def upgrade() -> None:
    if "device_id" not in _columns("web_push_subscriptions"):
        op.add_column("web_push_subscriptions", sa.Column("device_id", sa.String(), nullable=True))
    op.create_index(
        "ix_web_push_subscriptions_user_device",
        "web_push_subscriptions",
        ["user_key", "device_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_web_push_subscriptions_user_device", table_name="web_push_subscriptions")
