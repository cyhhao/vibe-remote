from __future__ import annotations

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

schema_meta = Table(
    "schema_meta",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", String, nullable=False),
)

scopes = Table(
    "scopes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("platform", String, nullable=False),
    Column("scope_type", String, nullable=False),
    Column("scope_id", String, nullable=False),
    Column("display_name", Text, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("platform", "scope_type", "scope_id", name="uq_scopes_platform_type_id"),
    Index("ix_scopes_platform_type", "platform", "scope_type"),
)

channel_settings = Table(
    "channel_settings",
    metadata,
    Column("scope_id", Integer, ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True),
    Column("enabled", Integer, nullable=False),
    Column("show_message_types_json", Text, nullable=False),
    Column("custom_cwd", Text, nullable=True),
    Column("routing_json", Text, nullable=False),
    Column("require_mention", Integer, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

guild_settings = Table(
    "guild_settings",
    metadata,
    Column("scope_id", Integer, ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True),
    Column("enabled", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

guild_policies = Table(
    "guild_policies",
    metadata,
    Column("platform", String, primary_key=True),
    Column("default_enabled", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

user_settings = Table(
    "user_settings",
    metadata,
    Column("scope_id", Integer, ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True),
    Column("is_admin", Integer, nullable=False),
    Column("bound_at", String, nullable=True),
    Column("enabled", Integer, nullable=False),
    Column("show_message_types_json", Text, nullable=False),
    Column("custom_cwd", Text, nullable=True),
    Column("routing_json", Text, nullable=False),
    Column("dm_chat_id", Text, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

bind_codes = Table(
    "bind_codes",
    metadata,
    Column("code", String, primary_key=True),
    Column("type", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("expires_at", String, nullable=True),
    Column("is_active", Integer, nullable=False),
    Column("used_by_json", Text, nullable=False),
)

agent_session_bindings = Table(
    "agent_session_bindings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("scope_key", String, nullable=False),
    Column("agent_name", String, nullable=False),
    Column("thread_id", String, nullable=False),
    Column("session_id", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("scope_key", "agent_name", "thread_id", name="uq_agent_session_binding"),
    Index("ix_agent_session_bindings_session_id", "session_id"),
    Index("ix_agent_session_bindings_scope_agent", "scope_key", "agent_name"),
)

active_threads = Table(
    "active_threads",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("scope_key", String, nullable=False),
    Column("channel_id", String, nullable=False),
    Column("thread_id", String, nullable=False),
    Column("last_active_at", Float, nullable=False),
    UniqueConstraint("scope_key", "channel_id", "thread_id", name="uq_active_thread"),
)

active_polls = Table(
    "active_polls",
    metadata,
    Column("opencode_session_id", String, primary_key=True),
    Column("base_session_id", String, nullable=False),
    Column("platform", String, nullable=False),
    Column("channel_id", String, nullable=False),
    Column("thread_id", String, nullable=False),
    Column("settings_key", String, nullable=False),
    Column("working_path", Text, nullable=False),
    Column("started_at", Float, nullable=False),
    Column("baseline_message_ids_json", Text, nullable=False),
    Column("seen_tool_calls_json", Text, nullable=False),
    Column("emitted_assistant_messages_json", Text, nullable=False),
    Column("ack_reaction_message_id", Text, nullable=True),
    Column("ack_reaction_emoji", Text, nullable=True),
    Column("typing_indicator_active", Integer, nullable=False),
    Column("context_token", Text, nullable=False),
    Column("processing_indicator_json", Text, nullable=False),
    Column("user_id", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

processed_messages = Table(
    "processed_messages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("channel_id", String, nullable=False),
    Column("thread_id", String, nullable=False),
    Column("message_id", String, nullable=False),
    Column("processed_at", String, nullable=False),
    UniqueConstraint("channel_id", "thread_id", "message_id", name="uq_processed_message"),
    Index("ix_processed_messages_thread_time", "channel_id", "thread_id", "processed_at"),
)

chat_sessions = Table(
    "chat_sessions",
    metadata,
    Column("id", String, primary_key=True),
    Column("platform", String, nullable=False),
    Column("scope_type", String, nullable=False),
    Column("scope_id", String, nullable=False),
    Column("thread_id", String, nullable=True),
    Column("agent_backend", String, nullable=True),
    Column("agent_name", String, nullable=True),
    Column("working_path", Text, nullable=True),
    Column("title", Text, nullable=True),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("last_message_at", String, nullable=True),
    Index("ix_chat_sessions_scope_updated", "platform", "scope_type", "scope_id", "updated_at"),
    Index("ix_chat_sessions_agent", "agent_backend", "agent_name"),
    Index("ix_chat_sessions_working_path", "working_path"),
)

session_messages = Table(
    "session_messages",
    metadata,
    Column("id", String, primary_key=True),
    Column("session_id", String, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
    Column("parent_message_id", String, nullable=True),
    Column("role", String, nullable=False),
    Column("source", String, nullable=False),
    Column("content_text", Text, nullable=True),
    Column("content_json", Text, nullable=False),
    Column("agent_backend", String, nullable=True),
    Column("agent_name", String, nullable=True),
    Column("native_message_id", String, nullable=True),
    Column("created_at", String, nullable=False),
    Index("ix_session_messages_session_time", "session_id", "created_at"),
    Index("ix_session_messages_native_message", "native_message_id"),
    Index("ix_session_messages_agent_time", "agent_backend", "agent_name", "created_at"),
)

discovered_chats = Table(
    "discovered_chats",
    metadata,
    Column("platform", String, primary_key=True),
    Column("chat_id", String, primary_key=True),
    Column("name", Text, nullable=False),
    Column("username", Text, nullable=False),
    Column("chat_type", String, nullable=False),
    Column("is_private", Integer, nullable=False),
    Column("is_forum", Integer, nullable=False),
    Column("supports_topics", Integer, nullable=False),
    Column("last_seen_at", String, nullable=False),
)

imported_state_tables = [
    session_messages,
    chat_sessions,
    channel_settings,
    guild_settings,
    guild_policies,
    user_settings,
    bind_codes,
    agent_session_bindings,
    active_threads,
    active_polls,
    processed_messages,
    discovered_chats,
    scopes,
]
