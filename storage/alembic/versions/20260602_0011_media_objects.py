"""media_objects proxy-token table for workbench chat media

Backs the same-origin media proxy that renders agent-reply images inline and
files as download cards in the avibe workbench Chat. A local file referenced by
an agent reply (``file://``) — or uploaded by the user — is registered here and
served over ``/api/sessions/<session_id>/media/<token>``; the URL carries only
the opaque token, never a path.

Revision ID: 20260602_0011
Revises: 20260531_0010
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260602_0011"
down_revision = "20260531_0010"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    bind = op.get_bind()
    return {row[0] for row in bind.exec_driver_sql("select name from sqlite_master where type = 'table'")}


def upgrade() -> None:
    if "media_objects" not in _tables():
        op.create_table(
            "media_objects",
            sa.Column("token", sa.String(), primary_key=True),
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
            sa.Column(
                "message_id",
                sa.String(),
                sa.ForeignKey("messages.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("local_path", sa.Text(), nullable=False),
            sa.Column("file_name", sa.Text(), nullable=True),
            sa.Column("content_type", sa.String(), nullable=True),
            sa.Column("file_ext", sa.String(), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("expires_at", sa.String(), nullable=True),
            sa.Column("revoked_at", sa.String(), nullable=True),
        )
    op.create_index("ix_media_objects_session", "media_objects", ["session_id"], if_not_exists=True)
    op.create_index(
        "ix_media_objects_scope_created",
        "media_objects",
        ["scope_id", "created_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("media_objects")
