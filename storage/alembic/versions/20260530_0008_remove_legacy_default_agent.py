"""remove legacy builtin default Agent

Revision ID: 20260530_0008
Revises: 20260529_0007
Create Date: 2026-05-30
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from alembic import op

revision = "20260530_0008"
down_revision = "20260529_0007"
branch_labels = None
depends_on = None

_BACKENDS = {"opencode", "claude", "codex"}
_LEGACY_DEFAULT_AGENT_NAME = "default"
_DEFAULT_AGENT_META_KEY = "default_agent_name"
_BUILTIN_DEFAULT_METADATA = {"builtin": True, "builtin_default": True, "lock_delete": True}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "agents" not in tables:
        return

    legacy = _legacy_default_agent(bind)
    if legacy is None or not _is_unmodified_legacy_default_agent(legacy):
        return

    backend = str(legacy["backend"])
    target = _backend_default_agent(bind, backend)
    if target is None:
        return

    _retarget_agent_references(bind, tables, legacy, target)
    _retarget_default_agent_pointer(bind, tables, target["name"])
    bind.exec_driver_sql("delete from agents where id = ?", (legacy["id"],))


def downgrade() -> None:
    pass


def _tables(bind) -> set[str]:
    return {row[0] for row in bind.exec_driver_sql("select name from sqlite_master where type = 'table'")}


def _columns(bind, table: str) -> set[str]:
    return {row[1] for row in bind.exec_driver_sql(f'pragma table_info("{table}")')}


def _legacy_default_agent(bind) -> dict[str, Any] | None:
    row = bind.exec_driver_sql(
        """
        select id, name, normalized_name, description, backend, model, reasoning_effort,
               system_prompt, enabled, source, source_ref, metadata_json
        from agents
        where normalized_name = ?
        limit 1
        """,
        (_LEGACY_DEFAULT_AGENT_NAME,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "normalized_name": row[2],
        "description": row[3],
        "backend": row[4],
        "model": row[5],
        "reasoning_effort": row[6],
        "system_prompt": row[7],
        "enabled": row[8],
        "source": row[9],
        "source_ref": row[10],
        "metadata": _json_loads(row[11], {}),
    }


def _is_unmodified_legacy_default_agent(agent: dict[str, Any]) -> bool:
    metadata = agent["metadata"]
    if not isinstance(metadata, dict):
        return False
    if metadata != {"builtin": True}:
        return False
    return (
        agent["name"] == _LEGACY_DEFAULT_AGENT_NAME
        and agent["normalized_name"] == _LEGACY_DEFAULT_AGENT_NAME
        and agent["backend"] in _BACKENDS
        and agent["source"] == "builtin"
        and _is_empty(agent["source_ref"])
        and agent["description"] == "Default Vibe Remote agent."
        and _is_empty(agent["model"])
        and _is_empty(agent["reasoning_effort"])
        and _is_empty(agent["system_prompt"])
    )


def _backend_default_agent(bind, backend: str) -> dict[str, Any] | None:
    row = bind.exec_driver_sql(
        """
        select id, name, backend, enabled, source, metadata_json
        from agents
        where normalized_name = ?
        limit 1
        """,
        (backend,),
    ).fetchone()
    if row is None:
        return _insert_backend_default_agent(bind, backend)

    agent_id, name, existing_backend, enabled, source, metadata_json = row
    metadata = _json_loads(metadata_json, {})
    if (
        existing_backend != backend
        or not bool(enabled)
        or source != "builtin"
        or not isinstance(metadata, dict)
        or metadata.get("backend") != backend
        or not bool(metadata.get("builtin_default") or metadata.get("lock_delete"))
    ):
        return None
    return {"id": agent_id, "name": str(name), "backend": existing_backend}


def _insert_backend_default_agent(bind, backend: str) -> dict[str, Any]:
    now = _utc_now_iso()
    metadata = {**_BUILTIN_DEFAULT_METADATA, "backend": backend, "backend_enabled": True}
    agent_id = uuid.uuid4().hex[:12]
    bind.exec_driver_sql(
        """
        insert into agents (
            id, name, normalized_name, description, backend, model, reasoning_effort,
            system_prompt, enabled, source, source_ref, metadata_json, created_at, updated_at
        ) values (?, ?, ?, ?, ?, null, null, null, 1, 'builtin', null, ?, ?, ?)
        """,
        (
            agent_id,
            backend,
            backend,
            f"Default {backend} agent.",
            backend,
            _json_dumps(metadata),
            now,
            now,
        ),
    )
    return {"id": agent_id, "name": backend, "backend": backend}


def _retarget_agent_references(bind, tables: set[str], legacy: dict[str, Any], target: dict[str, Any]) -> None:
    legacy_name = str(legacy["name"])
    legacy_id = str(legacy["id"])
    target_name = str(target["name"])
    target_id = str(target["id"])

    if "scope_settings" in tables:
        columns = _columns(bind, "scope_settings")
        if "scope_id" in columns and ("agent_name" in columns or "settings_json" in columns):
            _retarget_scope_settings(bind, columns, legacy_name, target_name)
        if "agent_variant" in columns:
            _retarget_agent_variant(bind, "scope_settings", legacy_name, target_name)
    if "agent_sessions" in tables:
        columns = _columns(bind, "agent_sessions")
        _retarget_agent_table(bind, "agent_sessions", columns, legacy_name, target_name, legacy_id, target_id)
    if "run_definitions" in tables:
        columns = _columns(bind, "run_definitions")
        _retarget_agent_table(bind, "run_definitions", columns, legacy_name, target_name, legacy_id, target_id)
    if "agent_runs" in tables:
        columns = _columns(bind, "agent_runs")
        _retarget_agent_table(bind, "agent_runs", columns, legacy_name, target_name, legacy_id, target_id)


def _retarget_scope_settings(bind, columns: set[str], legacy_name: str, target_name: str) -> None:
    agent_name_expr = "agent_name" if "agent_name" in columns else "null"
    settings_json_expr = "settings_json" if "settings_json" in columns else "null"
    rows = bind.exec_driver_sql(
        f"""
        select scope_id, {agent_name_expr}, {settings_json_expr}
        from scope_settings
        """
    ).fetchall()
    for scope_id, agent_name, settings_json in rows:
        assignments = []
        params = []
        settings_json_changed = False
        if "settings_json" in columns:
            retargeted_settings_json = _settings_json_retarget_agent(settings_json, legacy_name, target_name)
            settings_json_changed = retargeted_settings_json != settings_json
            if settings_json_changed:
                assignments.append("settings_json = ?")
                params.append(retargeted_settings_json)
        if "agent_name" in columns and (agent_name == legacy_name or (settings_json_changed and _is_empty(agent_name))):
            assignments.insert(0, "agent_name = ?")
            params.insert(0, target_name)
        if not assignments:
            continue
        bind.exec_driver_sql(
            f"""
            update scope_settings
            set {", ".join(assignments)}
            where scope_id = ?
            """,
            (*params, scope_id),
        )


def _retarget_agent_table(
    bind,
    table: str,
    columns: set[str],
    legacy_name: str,
    target_name: str,
    legacy_id: str,
    target_id: str,
) -> None:
    if "agent_name" in columns:
        bind.exec_driver_sql(
            f'update "{table}" set agent_name = ? where agent_name = ?',
            (target_name, legacy_name),
        )
    if "agent_id" in columns:
        bind.exec_driver_sql(
            f'update "{table}" set agent_id = ? where agent_id = ?',
            (target_id, legacy_id),
        )
    if "agent_variant" in columns:
        _retarget_agent_variant(bind, table, legacy_name, target_name)


def _retarget_agent_variant(bind, table: str, legacy_name: str, target_name: str) -> None:
    bind.exec_driver_sql(
        f'update "{table}" set agent_variant = ? where agent_variant = ?',
        (target_name, legacy_name),
    )


def _retarget_default_agent_pointer(bind, tables: set[str], target_name: str) -> None:
    if "state_meta" not in tables:
        return
    row = bind.exec_driver_sql(
        "select value_json from state_meta where key = ? limit 1",
        (_DEFAULT_AGENT_META_KEY,),
    ).fetchone()
    if row is None or _json_loads(row[0], None) != _LEGACY_DEFAULT_AGENT_NAME:
        return
    now = _utc_now_iso()
    bind.exec_driver_sql(
        "update state_meta set value_json = ?, updated_at = ? where key = ?",
        (_json_dumps(target_name), now, _DEFAULT_AGENT_META_KEY),
    )


def _settings_json_retarget_agent(value: str | None, legacy_name: str, target_name: str) -> str | None:
    payload = _json_loads(value, None)
    if not isinstance(payload, dict):
        return value
    routing = payload.get("routing")
    if not isinstance(routing, dict):
        return value
    changed = False
    if routing.get("agent_name") == legacy_name:
        routing["agent_name"] = target_name
        changed = True
    if routing.get("agent") == legacy_name:
        routing["agent"] = target_name
        changed = True
    if not changed:
        return value
    payload["routing"] = routing
    return _json_dumps(payload)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
