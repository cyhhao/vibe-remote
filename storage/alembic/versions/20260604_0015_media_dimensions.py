"""media_objects.width_px / height_px for zero-shift image rendering

``storage.media_service.register`` now reads an image's pixel dimensions (header
only, via ``imagesize``) when minting a media row and stores them here. The web
Chat uses them to reserve an image's box before it loads, so a late-loading image
never shifts the transcript while the user scrolls. Both columns are nullable:
non-images and any file whose dimensions could not be read stay NULL, and the UI
falls back to measuring the image once in the browser.

Revision ID: 20260604_0015
Revises: 20260603_0014
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260604_0015"
down_revision = "20260603_0014"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {row[1] for row in bind.exec_driver_sql(f"PRAGMA table_info({table})")}


def upgrade() -> None:
    existing = _columns("media_objects")
    if "width_px" not in existing:
        op.add_column("media_objects", sa.Column("width_px", sa.Integer(), nullable=True))
    if "height_px" not in existing:
        op.add_column("media_objects", sa.Column("height_px", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_objects", "height_px")
    op.drop_column("media_objects", "width_px")
