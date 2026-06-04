"""web_push_subscriptions for PWA notification endpoints

Stores per-device Push API subscriptions for the Workbench PWA. A subscription
is runtime state, not authored config: endpoints can rotate, expire, or be
disabled independently across installed browser apps.

Revision ID: 20260604_0016
Revises: 20260604_0015
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260604_0016"
down_revision = "20260604_0015"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    bind = op.get_bind()
    return {row[0] for row in bind.exec_driver_sql("select name from sqlite_master where type = 'table'")}


def upgrade() -> None:
    if "web_push_subscriptions" not in _tables():
        op.create_table(
            "web_push_subscriptions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_key", sa.String(), nullable=False),
            sa.Column("endpoint", sa.Text(), nullable=False),
            sa.Column("p256dh", sa.Text(), nullable=False),
            sa.Column("auth", sa.Text(), nullable=False),
            sa.Column("user_agent", sa.Text(), nullable=True),
            sa.Column("device_label", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Integer(), nullable=False),
            sa.Column("last_success_at", sa.String(), nullable=True),
            sa.Column("last_failure_at", sa.String(), nullable=True),
            sa.Column("failure_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
            sa.UniqueConstraint("endpoint", name="uq_web_push_subscriptions_endpoint"),
        )
    op.create_index(
        "ix_web_push_subscriptions_user_enabled",
        "web_push_subscriptions",
        ["user_key", "enabled"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("web_push_subscriptions")
