"""backfill scope Agent names from legacy backend routing

Revision ID: 20260529_0007
Revises: 20260526_0006
Create Date: 2026-05-29
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from alembic import op

revision = "20260529_0007"
down_revision = "20260526_0006"
branch_labels = None
depends_on = None

_BACKENDS = ("opencode", "claude", "codex")
_BUILTIN_DEFAULT_METADATA = {"builtin": True, "builtin_default": True, "lock_delete": True}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if not {"agents", "scopes", "scope_settings"}.issubset(tables):
        return

    now = _utc_now_iso()
    for backend in _BACKENDS:
        if not _scope_count_for_backend(bind, backend):
            continue
        agent_name = _ensure_backend_default_agent(bind, backend, now)
        if not agent_name:
            continue
        _backfill_scope_agent_name(bind, backend, agent_name, now)


def downgrade() -> None:
    pass


def _tables(bind) -> set[str]:
    return {row[0] for row in bind.exec_driver_sql("select name from sqlite_master where type = 'table'")}


def _scope_count_for_backend(bind, backend: str) -> int:
    return int(
        bind.exec_driver_sql(
            """
            select count(*)
            from scope_settings ss
            join scopes s on s.id = ss.scope_id
            where s.scope_type in ('channel', 'user')
              and ss.agent_backend = ?
              and (ss.agent_name is null or trim(ss.agent_name) = '')
            """,
            (backend,),
        ).scalar()
        or 0
    )


def _ensure_backend_default_agent(bind, backend: str, now: str) -> str | None:
    existing = bind.exec_driver_sql(
        "select name, backend, enabled from agents where normalized_name = ? limit 1",
        (backend,),
    ).fetchone()
    if existing:
        name, existing_backend, enabled = existing
        return str(name) if existing_backend == backend and bool(enabled) else None

    metadata = {**_BUILTIN_DEFAULT_METADATA, "backend": backend, "backend_enabled": True}
    bind.exec_driver_sql(
        """
        insert into agents (
            id, name, normalized_name, description, backend, model, reasoning_effort,
            system_prompt, enabled, source, source_ref, metadata_json, created_at, updated_at
        ) values (?, ?, ?, ?, ?, null, null, null, 1, 'builtin', null, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex[:12],
            backend,
            backend,
            f"Default {backend} agent.",
            backend,
            _json_dumps(metadata),
            now,
            now,
        ),
    )
    return backend


def _backfill_scope_agent_name(bind, backend: str, agent_name: str, now: str) -> None:
    rows = bind.exec_driver_sql(
        """
        select ss.scope_id, ss.settings_json
        from scope_settings ss
        join scopes s on s.id = ss.scope_id
        where s.scope_type in ('channel', 'user')
          and ss.agent_backend = ?
          and (ss.agent_name is null or trim(ss.agent_name) = '')
        """,
        (backend,),
    ).fetchall()
    for scope_id, settings_json in rows:
        bind.exec_driver_sql(
            """
            update scope_settings
            set agent_name = ?, settings_json = ?, updated_at = ?
            where scope_id = ?
            """,
            (agent_name, _settings_json_with_agent_name(settings_json, agent_name), now, scope_id),
        )


def _settings_json_with_agent_name(value: str | None, agent_name: str) -> str:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, ValueError):
        return value or "{}"
    if not isinstance(payload, dict):
        return value or "{}"
    routing = payload.get("routing")
    if not isinstance(routing, dict):
        routing = {}
    if not routing.get("agent_name"):
        routing["agent_name"] = agent_name
    payload["routing"] = routing
    return _json_dumps(payload)


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
