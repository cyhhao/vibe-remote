"""show page state

Revision ID: 20260522_0003
Revises: 20260515_0002
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260522_0003"
down_revision = "20260515_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "show_pages",
        sa.Column("session_id", sa.String(), primary_key=True),
        sa.Column("visibility", sa.String(), nullable=False),
        sa.Column("share_id", sa.String(), nullable=True),
        sa.Column("offline_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.UniqueConstraint("share_id", name="uq_show_pages_share_id"),
    )
    op.create_index("ix_show_pages_share_id", "show_pages", ["share_id"])
    op.create_index("ix_show_pages_visibility", "show_pages", ["visibility"])


def downgrade() -> None:
    op.drop_table("show_pages")
