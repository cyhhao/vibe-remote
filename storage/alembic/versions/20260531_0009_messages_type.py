"""add messages.type column

Adds a first-class ``type`` column to ``messages`` (user / assistant /
tool_call / notify / result), distinct from the coarse ``author``. Backfills
existing rows from ``author`` + the legacy ``content_json.kind`` so the
per-session inbox can preview the latest ``assistant`` reply.

Revision ID: 20260531_0009
Revises: 20260530_0008
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260531_0009"
down_revision = "20260530_0008"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {row[1] for row in bind.exec_driver_sql(f'pragma table_info("{table}")')}


def _tables(bind) -> set[str]:
    return {row[0] for row in bind.exec_driver_sql("select name from sqlite_master where type = 'table'")}


def upgrade() -> None:
    bind = op.get_bind()
    if "messages" not in _tables(bind):
        return
    if "type" in _columns(bind, "messages"):
        return

    # server_default lets the NOT NULL add succeed on existing rows; new
    # inserts always pass an explicit type via messages_service.append.
    op.add_column(
        "messages",
        sa.Column("type", sa.String(), nullable=False, server_default="assistant"),
    )

    # Backfill: user rows -> user; agent rows keep their legacy content_json
    # kind (notify/result) or fall back to assistant; everything else (system)
    # collapses to assistant so it still surfaces as a textual reply.
    bind.exec_driver_sql(
        """
        update messages
        set type = case
            when author = 'user' then 'user'
            when json_extract(content_json, '$.kind') = 'notify' then 'notify'
            when json_extract(content_json, '$.kind') = 'result' then 'result'
            when json_extract(content_json, '$.kind') = 'toolcall' then 'tool_call'
            when json_extract(content_json, '$.kind') = 'tool_call' then 'tool_call'
            else 'assistant'
        end
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "messages" in _tables(bind) and "type" in _columns(bind, "messages"):
        op.drop_column("messages", "type")
