"""agent catalog and run harness schema

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


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    return {row[1] for row in bind.exec_driver_sql(f'pragma table_info("{table_name}")')}


def _tables() -> set[str]:
    bind = op.get_bind()
    return {row[0] for row in bind.exec_driver_sql("select name from sqlite_master where type = 'table'")}


def _add_column_if_missing(table_name: str, column_name: str, column: sa.Column) -> None:
    if column_name not in _columns(table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    tables = _tables()
    if "agents" not in tables:
        op.create_table(
            "agents",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("normalized_name", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("backend", sa.String(), nullable=False),
            sa.Column("model", sa.String(), nullable=True),
            sa.Column("reasoning_effort", sa.String(), nullable=True),
            sa.Column("system_prompt", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("source_ref", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
            sa.UniqueConstraint("normalized_name", name="uq_agents_normalized_name"),
        )
    else:
        _add_column_if_missing("agents", "enabled", sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_agents_backend", "agents", ["backend"], if_not_exists=True)
    op.create_index("ix_agents_updated", "agents", ["updated_at"], if_not_exists=True)

    tables = _tables()
    if "background_tasks" in tables and "run_definitions" not in tables:
        op.rename_table("background_tasks", "run_definitions")
    if "background_runs" in tables and "agent_runs" not in tables:
        op.rename_table("background_runs", "agent_runs")

    if "scope_settings" in _tables():
        _add_column_if_missing("scope_settings", "agent_name", sa.Column("agent_name", sa.String(), nullable=True))

    if "agent_sessions" in _tables():
        _add_column_if_missing("agent_sessions", "agent_id", sa.Column("agent_id", sa.String(), nullable=True))
        _add_column_if_missing("agent_sessions", "agent_name", sa.Column("agent_name", sa.String(), nullable=True))

    if "run_definitions" in _tables():
        definition_columns = _columns("run_definitions")
        if "definition_type" not in definition_columns and "task_type" in definition_columns:
            op.alter_column("run_definitions", "task_type", new_column_name="definition_type")
        _add_column_if_missing("run_definitions", "agent_name", sa.Column("agent_name", sa.String(), nullable=True))
        _add_column_if_missing("run_definitions", "session_policy", sa.Column("session_policy", sa.String(), nullable=True))
        _add_column_if_missing("run_definitions", "message", sa.Column("message", sa.Text(), nullable=True))
        _add_column_if_missing(
            "run_definitions",
            "message_payload_json",
            sa.Column("message_payload_json", sa.Text(), nullable=True),
        )
        _add_column_if_missing("run_definitions", "last_run_id", sa.Column("last_run_id", sa.String(), nullable=True))
        _add_column_if_missing("run_definitions", "deleted_at", sa.Column("deleted_at", sa.String(), nullable=True))
        op.execute('update run_definitions set message = prompt where message is null')
        op.execute(
            "update run_definitions set session_policy = "
            "case "
            "when session_id is not null and session_id != '' then 'existing' "
            "when legacy_session_key is not null and legacy_session_key != '' then 'existing' "
            "else null end "
            "where session_policy is null"
        )
        op.create_index("ix_run_definitions_type_enabled", "run_definitions", ["definition_type", "enabled"], if_not_exists=True)
        op.create_index("ix_run_definitions_session", "run_definitions", ["session_id"], if_not_exists=True)
        op.create_index("ix_run_definitions_agent", "run_definitions", ["agent_name"], if_not_exists=True)
        op.create_index("ix_run_definitions_updated", "run_definitions", ["updated_at"], if_not_exists=True)

    if "agent_runs" in _tables():
        run_columns = _columns("agent_runs")
        if "definition_id" not in run_columns and "task_id" in run_columns:
            op.alter_column("agent_runs", "task_id", new_column_name="definition_id")
        _add_column_if_missing("agent_runs", "source_kind", sa.Column("source_kind", sa.String(), nullable=True))
        _add_column_if_missing("agent_runs", "source_actor", sa.Column("source_actor", sa.Text(), nullable=True))
        _add_column_if_missing("agent_runs", "parent_run_id", sa.Column("parent_run_id", sa.String(), nullable=True))
        _add_column_if_missing("agent_runs", "agent_name", sa.Column("agent_name", sa.String(), nullable=True))
        _add_column_if_missing("agent_runs", "agent_id", sa.Column("agent_id", sa.String(), nullable=True))
        _add_column_if_missing("agent_runs", "agent_backend", sa.Column("agent_backend", sa.String(), nullable=True))
        _add_column_if_missing("agent_runs", "model", sa.Column("model", sa.String(), nullable=True))
        _add_column_if_missing("agent_runs", "reasoning_effort", sa.Column("reasoning_effort", sa.String(), nullable=True))
        _add_column_if_missing("agent_runs", "session_policy", sa.Column("session_policy", sa.String(), nullable=True))
        _add_column_if_missing("agent_runs", "message", sa.Column("message", sa.Text(), nullable=True))
        _add_column_if_missing("agent_runs", "message_payload_json", sa.Column("message_payload_json", sa.Text(), nullable=True))
        _add_column_if_missing("agent_runs", "result_text", sa.Column("result_text", sa.Text(), nullable=True))
        _add_column_if_missing("agent_runs", "result_payload_json", sa.Column("result_payload_json", sa.Text(), nullable=True))
        _add_column_if_missing("agent_runs", "message_ids_json", sa.Column("message_ids_json", sa.Text(), nullable=True))
        _add_column_if_missing(
            "agent_runs",
            "cancel_requested",
            sa.Column("cancel_requested", sa.Integer(), nullable=False, server_default="0"),
        )
        _add_column_if_missing("agent_runs", "cancel_requested_at", sa.Column("cancel_requested_at", sa.String(), nullable=True))
        op.execute('update agent_runs set message = prompt where message is null')
        op.create_index("ix_agent_runs_definition_created", "agent_runs", ["definition_id", "created_at"], if_not_exists=True)
        op.create_index("ix_agent_runs_status_created", "agent_runs", ["status", "created_at"], if_not_exists=True)
        op.create_index(
            "ix_agent_runs_type_status_created",
            "agent_runs",
            ["run_type", "status", "created_at"],
            if_not_exists=True,
        )
        op.create_index("ix_agent_runs_session_created", "agent_runs", ["session_id", "created_at"], if_not_exists=True)
        op.create_index("ix_agent_runs_agent_created", "agent_runs", ["agent_name", "created_at"], if_not_exists=True)


def downgrade() -> None:
    pass
