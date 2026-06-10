"""agent run callback session fields

Revision ID: 20260610_0022
Revises: 20260608_0021
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260610_0022"
down_revision = "20260608_0021"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    return {row[1] for row in bind.exec_driver_sql(f'pragma table_info("{table_name}")')}


def _add_column_if_missing(table_name: str, column_name: str, column: sa.Column) -> None:
    if column_name not in _columns(table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing("agent_runs", "callback_session_id", sa.Column("callback_session_id", sa.String(), nullable=True))
    _add_column_if_missing("agent_runs", "callback_status", sa.Column("callback_status", sa.String(), nullable=True))
    _add_column_if_missing("agent_runs", "callback_error", sa.Column("callback_error", sa.Text(), nullable=True))
    _add_column_if_missing("agent_runs", "callback_run_id", sa.Column("callback_run_id", sa.String(), nullable=True))
    _add_column_if_missing("agent_runs", "callback_completed_at", sa.Column("callback_completed_at", sa.String(), nullable=True))
    op.create_index("ix_agent_runs_callback_status", "agent_runs", ["callback_status", "completed_at"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_agent_runs_callback_status", table_name="agent_runs", if_exists=True)
    # SQLite cannot drop columns without table rebuild; keep additive fields on downgrade.
    return None

