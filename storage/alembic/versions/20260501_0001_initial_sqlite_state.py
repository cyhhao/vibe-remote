"""initial sqlite state schema

Revision ID: 20260501_0001
Revises:
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260501_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "state_meta",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "scopes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("native_id", sa.String(), nullable=False),
        sa.Column("parent_scope_id", sa.String(), sa.ForeignKey("scopes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("native_type", sa.String(), nullable=True),
        sa.Column("is_private", sa.Integer(), nullable=False),
        sa.Column("supports_threads", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.String(), nullable=False),
        sa.Column("last_seen_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.UniqueConstraint("platform", "scope_type", "native_id", name="uq_scopes_platform_type_native"),
    )
    op.create_index("ix_scopes_platform_type", "scopes", ["platform", "scope_type"])
    op.create_index("ix_scopes_parent", "scopes", ["parent_scope_id"])

    op.create_table(
        "scope_settings",
        sa.Column("scope_id", sa.String(), sa.ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("workdir", sa.Text(), nullable=True),
        sa.Column("agent_backend", sa.String(), nullable=True),
        sa.Column("agent_variant", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("reasoning_effort", sa.String(), nullable=True),
        sa.Column("require_mention", sa.Integer(), nullable=True),
        sa.Column("settings_version", sa.Integer(), nullable=False),
        sa.Column("settings_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index("ix_scope_settings_role", "scope_settings", ["role"])
    op.create_index("ix_scope_settings_workdir", "scope_settings", ["workdir"])
    op.create_index("ix_scope_settings_backend_model", "scope_settings", ["agent_backend", "model"])

    op.create_table(
        "auth_codes",
        sa.Column("code", sa.String(), primary_key=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("is_active", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.Column("used_by_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )

    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("scope_id", sa.String(), sa.ForeignKey("scopes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_backend", sa.String(), nullable=False),
        sa.Column("agent_variant", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("reasoning_effort", sa.String(), nullable=True),
        sa.Column("session_anchor", sa.String(), nullable=False),
        sa.Column("workdir", sa.Text(), nullable=True),
        sa.Column("native_session_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("last_active_at", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_agent_sessions_scope_anchor_workdir",
        "agent_sessions",
        ["scope_id", "session_anchor", "workdir"],
    )
    op.create_index("ix_agent_sessions_backend_variant", "agent_sessions", ["agent_backend", "agent_variant"])
    op.create_index("ix_agent_sessions_status_activity", "agent_sessions", ["status", "last_active_at"])
    op.create_index("ix_agent_sessions_native_session", "agent_sessions", ["native_session_id"])

    op.create_table(
        "runtime_records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("record_type", sa.String(), nullable=False),
        sa.Column("record_key", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), sa.ForeignKey("scopes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("session_anchor", sa.String(), nullable=True),
        sa.Column("workdir", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.UniqueConstraint("record_type", "record_key", name="uq_runtime_records_type_key"),
    )
    op.create_index(
        "ix_runtime_records_type_scope_expiry",
        "runtime_records",
        ["record_type", "scope_id", "expires_at"],
    )
    op.create_index("ix_runtime_records_scope_anchor", "runtime_records", ["scope_id", "session_anchor"])
    op.create_index("ix_runtime_records_workdir", "runtime_records", ["workdir"])


def downgrade() -> None:
    op.drop_table("runtime_records")
    op.drop_table("agent_sessions")
    op.drop_table("auth_codes")
    op.drop_table("scope_settings")
    op.drop_table("scopes")
    op.drop_table("state_meta")
