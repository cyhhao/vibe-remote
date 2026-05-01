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
        "schema_meta",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "scopes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.UniqueConstraint("platform", "scope_type", "scope_id", name="uq_scopes_platform_type_id"),
    )
    op.create_index("ix_scopes_platform_type", "scopes", ["platform", "scope_type"])

    op.create_table(
        "channel_settings",
        sa.Column("scope_id", sa.Integer(), sa.ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("show_message_types_json", sa.Text(), nullable=False),
        sa.Column("custom_cwd", sa.Text(), nullable=True),
        sa.Column("routing_json", sa.Text(), nullable=False),
        sa.Column("require_mention", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "guild_settings",
        sa.Column("scope_id", sa.Integer(), sa.ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "guild_policies",
        sa.Column("platform", sa.String(), primary_key=True),
        sa.Column("default_enabled", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "user_settings",
        sa.Column("scope_id", sa.Integer(), sa.ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("is_admin", sa.Integer(), nullable=False),
        sa.Column("bound_at", sa.String(), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("show_message_types_json", sa.Text(), nullable=False),
        sa.Column("custom_cwd", sa.Text(), nullable=True),
        sa.Column("routing_json", sa.Text(), nullable=False),
        sa.Column("dm_chat_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "bind_codes",
        sa.Column("code", sa.String(), primary_key=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.Column("is_active", sa.Integer(), nullable=False),
        sa.Column("used_by_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "agent_session_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.UniqueConstraint("scope_key", "agent_name", "thread_id", name="uq_agent_session_binding"),
    )
    op.create_index("ix_agent_session_bindings_session_id", "agent_session_bindings", ["session_id"])
    op.create_index(
        "ix_agent_session_bindings_scope_agent",
        "agent_session_bindings",
        ["scope_key", "agent_name"],
    )

    op.create_table(
        "active_threads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("channel_id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("last_active_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("scope_key", "channel_id", "thread_id", name="uq_active_thread"),
    )
    op.create_table(
        "active_polls",
        sa.Column("opencode_session_id", sa.String(), primary_key=True),
        sa.Column("base_session_id", sa.String(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("channel_id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("settings_key", sa.String(), nullable=False),
        sa.Column("working_path", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Float(), nullable=False),
        sa.Column("baseline_message_ids_json", sa.Text(), nullable=False),
        sa.Column("seen_tool_calls_json", sa.Text(), nullable=False),
        sa.Column("emitted_assistant_messages_json", sa.Text(), nullable=False),
        sa.Column("ack_reaction_message_id", sa.Text(), nullable=True),
        sa.Column("ack_reaction_emoji", sa.Text(), nullable=True),
        sa.Column("typing_indicator_active", sa.Integer(), nullable=False),
        sa.Column("context_token", sa.Text(), nullable=False),
        sa.Column("processing_indicator_json", sa.Text(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "processed_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel_id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("processed_at", sa.String(), nullable=False),
        sa.UniqueConstraint("channel_id", "thread_id", "message_id", name="uq_processed_message"),
    )
    op.create_index(
        "ix_processed_messages_thread_time",
        "processed_messages",
        ["channel_id", "thread_id", "processed_at"],
    )

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=True),
        sa.Column("agent_backend", sa.String(), nullable=True),
        sa.Column("agent_name", sa.String(), nullable=True),
        sa.Column("working_path", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("last_message_at", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_chat_sessions_scope_updated",
        "chat_sessions",
        ["platform", "scope_type", "scope_id", "updated_at"],
    )
    op.create_index("ix_chat_sessions_agent", "chat_sessions", ["agent_backend", "agent_name"])
    op.create_index("ix_chat_sessions_working_path", "chat_sessions", ["working_path"])

    op.create_table(
        "session_messages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_message_id", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("agent_backend", sa.String(), nullable=True),
        sa.Column("agent_name", sa.String(), nullable=True),
        sa.Column("native_message_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    op.create_index("ix_session_messages_session_time", "session_messages", ["session_id", "created_at"])
    op.create_index("ix_session_messages_native_message", "session_messages", ["native_message_id"])
    op.create_index(
        "ix_session_messages_agent_time",
        "session_messages",
        ["agent_backend", "agent_name", "created_at"],
    )

    op.create_table(
        "discovered_chats",
        sa.Column("platform", sa.String(), primary_key=True),
        sa.Column("chat_id", sa.String(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("chat_type", sa.String(), nullable=False),
        sa.Column("is_private", sa.Integer(), nullable=False),
        sa.Column("is_forum", sa.Integer(), nullable=False),
        sa.Column("supports_topics", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("discovered_chats")
    op.drop_table("session_messages")
    op.drop_table("chat_sessions")
    op.drop_table("processed_messages")
    op.drop_table("active_polls")
    op.drop_table("active_threads")
    op.drop_table("agent_session_bindings")
    op.drop_table("bind_codes")
    op.drop_table("user_settings")
    op.drop_table("guild_policies")
    op.drop_table("guild_settings")
    op.drop_table("channel_settings")
    op.drop_table("scopes")
    op.drop_table("schema_meta")
