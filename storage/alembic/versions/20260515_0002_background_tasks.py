"""background task and run tables

Revision ID: 20260515_0002
Revises: 20260501_0001
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260515_0002"
down_revision = "20260501_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_tasks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("legacy_session_key", sa.Text(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("schedule_type", sa.String(), nullable=True),
        sa.Column("cron", sa.Text(), nullable=True),
        sa.Column("run_at", sa.String(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column("command_json", sa.Text(), nullable=True),
        sa.Column("shell_command", sa.Text(), nullable=True),
        sa.Column("prefix", sa.Text(), nullable=True),
        sa.Column("cwd", sa.Text(), nullable=True),
        sa.Column("mode", sa.String(), nullable=True),
        sa.Column("timeout_seconds", sa.Float(), nullable=True),
        sa.Column("lifetime_timeout_seconds", sa.Float(), nullable=True),
        sa.Column("retry_exit_codes_json", sa.Text(), nullable=True),
        sa.Column("retry_delay_seconds", sa.Float(), nullable=True),
        sa.Column("post_to", sa.String(), nullable=True),
        sa.Column("deliver_key", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("last_started_at", sa.String(), nullable=True),
        sa.Column("last_finished_at", sa.String(), nullable=True),
        sa.Column("last_event_at", sa.String(), nullable=True),
        sa.Column("last_run_at", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_exit_code", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False),
    )
    op.create_index("ix_background_tasks_type_enabled", "background_tasks", ["task_type", "enabled"])
    op.create_index("ix_background_tasks_session", "background_tasks", ["session_id"])
    op.create_index("ix_background_tasks_updated", "background_tasks", ["updated_at"])

    op.create_table(
        "background_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("run_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("legacy_session_key", sa.Text(), nullable=True),
        sa.Column("post_to", sa.String(), nullable=True),
        sa.Column("deliver_key", sa.Text(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("stdout", sa.Text(), nullable=True),
        sa.Column("stderr", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
    )
    op.create_index("ix_background_runs_task_created", "background_runs", ["task_id", "created_at"])
    op.create_index("ix_background_runs_status", "background_runs", ["status"])
    op.create_index("ix_background_runs_session_created", "background_runs", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_table("background_runs")
    op.drop_table("background_tasks")
