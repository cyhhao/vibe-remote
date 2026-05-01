from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, func, select

from config import paths
from config.discovered_chats import DiscoveredChatsStore
from config.v2_sessions import SessionsStore
from config.v2_settings import SettingsState, _split_scoped_key, load_settings_state_from_json
from storage.db import create_sqlite_engine
from storage.lock import MigrationFileLock
from storage.migrations import run_migrations
from storage.models import (
    active_polls,
    active_threads,
    agent_session_bindings,
    bind_codes,
    channel_settings,
    discovered_chats,
    guild_policies,
    guild_settings,
    imported_state_tables,
    processed_messages,
    schema_meta,
    scopes,
    user_settings,
)

JSON_IMPORT_MARKER = "json_import_completed_at"


@dataclass(frozen=True)
class MigrationImportReport:
    db_path: Path
    imported: bool
    backup_path: Path | None = None
    counts: dict[str, int] = field(default_factory=dict)


def ensure_sqlite_state(
    *,
    db_path: Path | None = None,
    state_dir: Path | None = None,
    primary_platform: str = "slack",
) -> MigrationImportReport:
    """Create/migrate the SQLite DB and import existing JSON state once."""

    paths.ensure_data_dirs()
    target_db = (db_path or paths.get_sqlite_state_path()).expanduser().resolve()
    target_state_dir = (state_dir or paths.get_state_dir()).expanduser().resolve()
    lock_path = target_state_dir / "migration.lock"

    with MigrationFileLock(lock_path):
        run_migrations(target_db)
        engine = create_sqlite_engine(target_db)
        try:
            with engine.begin() as conn:
                if _has_import_marker(conn):
                    return MigrationImportReport(
                        db_path=target_db,
                        imported=False,
                        counts=_current_counts(conn),
                    )

            backup_path = _backup_json_state(target_state_dir)
            parsed = _parse_json_state(target_state_dir, primary_platform=primary_platform)

            with engine.begin() as conn:
                _clear_imported_state(conn)
                counts = _import_parsed_state(conn, parsed)
                _validate_import(conn, counts)
                _set_import_marker(conn)
                return MigrationImportReport(
                    db_path=target_db,
                    imported=True,
                    backup_path=backup_path,
                    counts=counts,
                )
        finally:
            engine.dispose()


def _has_import_marker(conn: Connection) -> bool:
    return (
        conn.execute(select(schema_meta.c.value).where(schema_meta.c.key == JSON_IMPORT_MARKER)).scalar_one_or_none()
        is not None
    )


def _set_import_marker(conn: Connection) -> None:
    now = _utc_now_iso()
    conn.execute(
        schema_meta.insert().values(
            key=JSON_IMPORT_MARKER,
            value=now,
            updated_at=now,
        )
    )


def _backup_json_state(state_dir: Path) -> Path:
    backups_dir = state_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backups_dir / f"sqlite-state-migration-{timestamp}"
    suffix = 1
    while backup_path.exists():
        suffix += 1
        backup_path = backups_dir / f"sqlite-state-migration-{timestamp}-{suffix}"
    backup_path.mkdir(parents=True)

    manifest: dict[str, Any] = {"created_at": _utc_now_iso(), "files": {}}
    for name in ("settings.json", "sessions.json", "discovered_chats.json"):
        source = state_dir / name
        if not source.exists():
            manifest["files"][name] = {"present": False}
            continue
        target = backup_path / name
        shutil.copy2(source, target)
        stat = source.stat()
        manifest["files"][name] = {
            "present": True,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }

    (backup_path / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return backup_path


@dataclass
class _ParsedState:
    settings: SettingsState
    sessions: SessionsStore
    discovered: DiscoveredChatsStore


def _parse_json_state(state_dir: Path, *, primary_platform: str) -> _ParsedState:
    settings = _load_settings_from_copy(state_dir / "settings.json")
    sessions = _load_sessions_from_copy(state_dir / "sessions.json", primary_platform=primary_platform)
    discovered = DiscoveredChatsStore(state_dir / "discovered_chats.json")
    return _ParsedState(settings=settings, sessions=sessions, discovered=discovered)


def _load_settings_from_copy(source: Path) -> SettingsState:
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "settings.json"
        if source.exists():
            shutil.copy2(source, target)
        state, _migrated = load_settings_state_from_json(target)
        return state


def _load_sessions_from_copy(source: Path, *, primary_platform: str) -> SessionsStore:
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "sessions.json"
        if source.exists():
            shutil.copy2(source, target)
        store = SessionsStore(target)
        store.load()
        store.migrate_active_polls(primary_platform)
        store.migrate_session_mappings(primary_platform)
        return store


def _clear_imported_state(conn: Connection) -> None:
    for table in imported_state_tables:
        conn.execute(table.delete())
    conn.execute(schema_meta.delete().where(schema_meta.c.key == JSON_IMPORT_MARKER))


def _import_parsed_state(conn: Connection, parsed: _ParsedState) -> dict[str, int]:
    now = _utc_now_iso()
    counts = {
        "channel_settings": 0,
        "guild_settings": 0,
        "guild_policies": 0,
        "user_settings": 0,
        "bind_codes": 0,
        "agent_session_bindings": 0,
        "active_threads": 0,
        "active_polls": 0,
        "processed_messages": 0,
        "discovered_chats": 0,
    }

    settings_state = parsed.settings

    for scoped_key, item in settings_state.channels.items():
        platform, channel_id = _split_scoped_key(scoped_key)
        scope_id = _get_or_create_scope(conn, platform or "unknown", "channel", channel_id, now=now)
        conn.execute(
            channel_settings.insert().values(
                scope_id=scope_id,
                enabled=_bool_int(item.enabled),
                show_message_types_json=_json_dumps(item.show_message_types),
                custom_cwd=item.custom_cwd,
                routing_json=_json_dumps(_asdict(item.routing)),
                require_mention=_nullable_bool_int(item.require_mention),
                created_at=now,
                updated_at=now,
            )
        )
        counts["channel_settings"] += 1

    for platform in sorted(settings_state.guild_scope_platforms):
        conn.execute(
            guild_policies.insert().values(
                platform=platform,
                default_enabled=_bool_int(settings_state.guild_default_enabled.get(platform, False)),
                created_at=now,
                updated_at=now,
            )
        )
        counts["guild_policies"] += 1

    for scoped_key, item in settings_state.guilds.items():
        platform, guild_id = _split_scoped_key(scoped_key)
        scope_id = _get_or_create_scope(conn, platform or "discord", "guild", guild_id, now=now)
        conn.execute(
            guild_settings.insert().values(
                scope_id=scope_id,
                enabled=_bool_int(item.enabled),
                created_at=now,
                updated_at=now,
            )
        )
        counts["guild_settings"] += 1

    for scoped_key, item in settings_state.users.items():
        platform, user_id = _split_scoped_key(scoped_key)
        scope_id = _get_or_create_scope(
            conn,
            platform or "unknown",
            "user",
            user_id,
            display_name=item.display_name,
            now=now,
        )
        conn.execute(
            user_settings.insert().values(
                scope_id=scope_id,
                is_admin=_bool_int(item.is_admin),
                bound_at=item.bound_at or None,
                enabled=_bool_int(item.enabled),
                show_message_types_json=_json_dumps(item.show_message_types),
                custom_cwd=item.custom_cwd,
                routing_json=_json_dumps(_asdict(item.routing)),
                dm_chat_id=item.dm_chat_id or None,
                created_at=now,
                updated_at=now,
            )
        )
        counts["user_settings"] += 1

    for item in settings_state.bind_codes:
        conn.execute(
            bind_codes.insert().values(
                code=item.code,
                type=item.type,
                created_at=item.created_at or now,
                expires_at=item.expires_at,
                is_active=_bool_int(item.is_active),
                used_by_json=_json_dumps(item.used_by),
            )
        )
        counts["bind_codes"] += 1

    session_state = parsed.sessions.state
    for scope_key, agent_maps in session_state.session_mappings.items():
        for agent_name, thread_map in agent_maps.items():
            for thread_id, session_id in thread_map.items():
                conn.execute(
                    agent_session_bindings.insert().values(
                        scope_key=str(scope_key),
                        agent_name=str(agent_name),
                        thread_id=str(thread_id),
                        session_id=str(session_id),
                        created_at=now,
                        updated_at=now,
                    )
                )
                counts["agent_session_bindings"] += 1

    for scope_key, channel_map in session_state.active_slack_threads.items():
        for channel_id, thread_map in channel_map.items():
            for thread_id, last_active_at in thread_map.items():
                conn.execute(
                    active_threads.insert().values(
                        scope_key=str(scope_key),
                        channel_id=str(channel_id),
                        thread_id=str(thread_id),
                        last_active_at=float(last_active_at),
                    )
                )
                counts["active_threads"] += 1

    for opencode_session_id, item in session_state.active_polls.items():
        if not isinstance(item, dict):
            continue
        conn.execute(
            active_polls.insert().values(
                opencode_session_id=str(opencode_session_id),
                base_session_id=str(item.get("base_session_id") or ""),
                platform=str(item.get("platform") or ""),
                channel_id=str(item.get("channel_id") or ""),
                thread_id=str(item.get("thread_id") or ""),
                settings_key=str(item.get("settings_key") or ""),
                working_path=str(item.get("working_path") or ""),
                started_at=float(item.get("started_at") or 0.0),
                baseline_message_ids_json=_json_dumps(item.get("baseline_message_ids") or []),
                seen_tool_calls_json=_json_dumps(item.get("seen_tool_calls") or []),
                emitted_assistant_messages_json=_json_dumps(item.get("emitted_assistant_messages") or []),
                ack_reaction_message_id=item.get("ack_reaction_message_id"),
                ack_reaction_emoji=item.get("ack_reaction_emoji"),
                typing_indicator_active=_bool_int(item.get("typing_indicator_active", False)),
                context_token=str(item.get("context_token") or ""),
                processing_indicator_json=_json_dumps(item.get("processing_indicator") or {}),
                user_id=str(item.get("user_id") or ""),
                updated_at=now,
            )
        )
        counts["active_polls"] += 1

    for channel_id, thread_map in session_state.processed_message_ts.items():
        if not isinstance(thread_map, dict):
            continue
        for thread_id, value in thread_map.items():
            message_ids = [value] if isinstance(value, str) else list(value or [])
            for message_id in message_ids[-200:]:
                result = conn.execute(
                    processed_messages.insert().values(
                        channel_id=str(channel_id),
                        thread_id=str(thread_id),
                        message_id=str(message_id),
                        processed_at=now,
                    ).prefix_with("OR IGNORE")
                )
                counts["processed_messages"] += int(result.rowcount or 0)

    for platform, chats in parsed.discovered.state.chats.items():
        for chat_id, chat in chats.items():
            conn.execute(
                discovered_chats.insert().values(
                    platform=str(platform),
                    chat_id=str(chat_id),
                    name=chat.name,
                    username=chat.username,
                    chat_type=chat.chat_type,
                    is_private=_bool_int(chat.is_private),
                    is_forum=_bool_int(chat.is_forum),
                    supports_topics=_bool_int(chat.supports_topics),
                    last_seen_at=chat.last_seen_at,
                )
            )
            counts["discovered_chats"] += 1

    return counts


def _get_or_create_scope(
    conn: Connection,
    platform: str,
    scope_type: str,
    scope_id: str,
    *,
    now: str,
    display_name: str | None = None,
) -> int:
    existing = conn.execute(
        select(scopes.c.id).where(
            scopes.c.platform == platform,
            scopes.c.scope_type == scope_type,
            scopes.c.scope_id == scope_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if display_name:
            conn.execute(scopes.update().where(scopes.c.id == existing).values(display_name=display_name, updated_at=now))
        return int(existing)

    result = conn.execute(
        scopes.insert().values(
            platform=platform,
            scope_type=scope_type,
            scope_id=scope_id,
            display_name=display_name,
            created_at=now,
            updated_at=now,
        )
    )
    return int(result.inserted_primary_key[0])


def _validate_import(conn: Connection, counts: dict[str, int]) -> None:
    integrity = conn.exec_driver_sql("PRAGMA integrity_check").scalar_one()
    if integrity != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {integrity}")
    current = _current_counts(conn)
    mismatches = {
        key: {"expected": value, "actual": current.get(key)}
        for key, value in counts.items()
        if current.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"SQLite JSON import count mismatch: {mismatches}")


def _current_counts(conn: Connection) -> dict[str, int]:
    tables = {
        "channel_settings": channel_settings,
        "guild_settings": guild_settings,
        "guild_policies": guild_policies,
        "user_settings": user_settings,
        "bind_codes": bind_codes,
        "agent_session_bindings": agent_session_bindings,
        "active_threads": active_threads,
        "active_polls": active_polls,
        "processed_messages": processed_messages,
        "discovered_chats": discovered_chats,
    }
    return {key: int(conn.execute(select(func.count()).select_from(table)).scalar_one()) for key, table in tables.items()}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _asdict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return dict(value)


def _bool_int(value: Any) -> int:
    return 1 if bool(value) else 0


def _nullable_bool_int(value: Any) -> int | None:
    return None if value is None else _bool_int(value)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
