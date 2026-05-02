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

state_meta = Table(
    "state_meta",
    metadata,
    Column("key", String, primary_key=True),
    Column("value_json", Text, nullable=False),
    Column("updated_at", String, nullable=False),
)

scopes = Table(
    "scopes",
    metadata,
    Column("id", String, primary_key=True),
    Column("platform", String, nullable=False),
    Column("scope_type", String, nullable=False),
    Column("native_id", String, nullable=False),
    Column("parent_scope_id", String, ForeignKey("scopes.id", ondelete="SET NULL"), nullable=True),
    Column("display_name", Text, nullable=True),
    Column("native_type", String, nullable=True),
    Column("is_private", Integer, nullable=False),
    Column("supports_threads", Integer, nullable=False),
    Column("metadata_json", Text, nullable=False),
    Column("first_seen_at", String, nullable=False),
    Column("last_seen_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("platform", "scope_type", "native_id", name="uq_scopes_platform_type_native"),
    Index("ix_scopes_platform_type", "platform", "scope_type"),
    Index("ix_scopes_parent", "parent_scope_id"),
)

scope_settings = Table(
    "scope_settings",
    metadata,
    Column("scope_id", String, ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True),
    Column("enabled", Integer, nullable=False),
    Column("role", String, nullable=True),
    Column("workdir", Text, nullable=True),
    Column("agent_backend", String, nullable=True),
    Column("agent_variant", String, nullable=True),
    Column("model", String, nullable=True),
    Column("reasoning_effort", String, nullable=True),
    Column("require_mention", Integer, nullable=True),
    Column("settings_version", Integer, nullable=False),
    Column("settings_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Index("ix_scope_settings_role", "role"),
    Index("ix_scope_settings_workdir", "workdir"),
    Index("ix_scope_settings_backend_model", "agent_backend", "model"),
)

auth_codes = Table(
    "auth_codes",
    metadata,
    Column("code", String, primary_key=True),
    Column("type", String, nullable=False),
    Column("is_active", Integer, nullable=False),
    Column("expires_at", String, nullable=True),
    Column("used_by_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

agent_sessions = Table(
    "agent_sessions",
    metadata,
    Column("id", String, primary_key=True),
    Column("scope_id", String, ForeignKey("scopes.id", ondelete="SET NULL"), nullable=True),
    Column("agent_backend", String, nullable=False),
    Column("agent_variant", String, nullable=False),
    Column("model", String, nullable=True),
    Column("reasoning_effort", String, nullable=True),
    Column("session_anchor", String, nullable=False),
    Column("workdir", Text, nullable=True),
    Column("native_session_id", Text, nullable=False),
    Column("title", Text, nullable=True),
    Column("status", String, nullable=False),
    Column("metadata_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("last_active_at", String, nullable=True),
    Index("ix_agent_sessions_scope_anchor_workdir", "scope_id", "session_anchor", "workdir"),
    Index("ix_agent_sessions_backend_variant", "agent_backend", "agent_variant"),
    Index("ix_agent_sessions_status_activity", "status", "last_active_at"),
    Index("ix_agent_sessions_native_session", "native_session_id"),
)

runtime_records = Table(
    "runtime_records",
    metadata,
    Column("id", String, primary_key=True),
    Column("record_type", String, nullable=False),
    Column("record_key", String, nullable=False),
    Column("scope_id", String, ForeignKey("scopes.id", ondelete="SET NULL"), nullable=True),
    Column("session_anchor", String, nullable=True),
    Column("workdir", Text, nullable=True),
    Column("payload_json", Text, nullable=False),
    Column("expires_at", String, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("record_type", "record_key", name="uq_runtime_records_type_key"),
    Index("ix_runtime_records_type_scope_expiry", "record_type", "scope_id", "expires_at"),
    Index("ix_runtime_records_scope_anchor", "scope_id", "session_anchor"),
    Index("ix_runtime_records_workdir", "workdir"),
)

imported_state_tables = [
    scope_settings,
    auth_codes,
    agent_sessions,
    runtime_records,
    scopes,
]
