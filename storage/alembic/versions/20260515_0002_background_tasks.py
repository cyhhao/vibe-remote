"""run definition and agent run tables

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
        "run_definitions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("definition_type", sa.String(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("agent_name", sa.String(), nullable=True),
        sa.Column("session_policy", sa.String(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("legacy_session_key", sa.Text(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("message_payload_json", sa.Text(), nullable=True),
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
        sa.Column("deleted_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("last_started_at", sa.String(), nullable=True),
        sa.Column("last_finished_at", sa.String(), nullable=True),
        sa.Column("last_event_at", sa.String(), nullable=True),
        sa.Column("last_run_at", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_exit_code", sa.Integer(), nullable=True),
        sa.Column("last_run_id", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False),
    )
    op.create_index("ix_run_definitions_type_enabled", "run_definitions", ["definition_type", "enabled"])
    op.create_index("ix_run_definitions_session", "run_definitions", ["session_id"])
    op.create_index("ix_run_definitions_agent", "run_definitions", ["agent_name"])
    op.create_index("ix_run_definitions_updated", "run_definitions", ["updated_at"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("definition_id", sa.String(), nullable=True),
        sa.Column("run_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=True),
        sa.Column("source_actor", sa.Text(), nullable=True),
        sa.Column("parent_run_id", sa.String(), nullable=True),
        sa.Column("agent_name", sa.String(), nullable=True),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("agent_backend", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("reasoning_effort", sa.String(), nullable=True),
        sa.Column("session_policy", sa.String(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("legacy_session_key", sa.Text(), nullable=True),
        sa.Column("post_to", sa.String(), nullable=True),
        sa.Column("deliver_key", sa.Text(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("message_payload_json", sa.Text(), nullable=True),
        sa.Column("result_text", sa.Text(), nullable=True),
        sa.Column("result_payload_json", sa.Text(), nullable=True),
        sa.Column("message_ids_json", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested_at", sa.String(), nullable=True),
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
    op.create_index("ix_agent_runs_definition_created", "agent_runs", ["definition_id", "created_at"])
    op.create_index("ix_agent_runs_status_created", "agent_runs", ["status", "created_at"])
    op.create_index("ix_agent_runs_type_status_created", "agent_runs", ["run_type", "status", "created_at"])
    op.create_index("ix_agent_runs_session_created", "agent_runs", ["session_id", "created_at"])
    op.create_index("ix_agent_runs_agent_created", "agent_runs", ["agent_name", "created_at"])


def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("run_definitions")
