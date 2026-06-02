"""media_objects.mtime_ns for machine-global content-fingerprint dedup

``storage.media_service.register`` now reuses an existing token for the same
``(local_path, size_bytes, mtime_ns)`` instead of minting a new one per
reference, so a re-referenced file keeps one stable ``/api/media/<token>`` URL
and the browser can cache it. ``mtime_ns`` + ``size_bytes`` is the change
fingerprint (stat-only, no file read); a rewrite bumps mtime so changed content
gets a fresh token/URL. The dedup index backs the lookup.

Revision ID: 20260603_0014
Revises: 20260602_0013
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260603_0014"
down_revision = "20260602_0013"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {row[1] for row in bind.exec_driver_sql(f"PRAGMA table_info({table})")}


def upgrade() -> None:
    if "mtime_ns" not in _columns("media_objects"):
        op.add_column("media_objects", sa.Column("mtime_ns", sa.Integer(), nullable=True))
    op.create_index(
        "ix_media_objects_dedup",
        "media_objects",
        ["local_path", "size_bytes", "mtime_ns"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_media_objects_dedup", table_name="media_objects")
    op.drop_column("media_objects", "mtime_ns")
