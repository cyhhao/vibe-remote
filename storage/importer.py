from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, func, select

from config import paths
from config.discovered_chats import DiscoveredChatsStore
from config.v2_sessions import (
    SessionState,
    infer_platform_from_thread_ids,
    load_session_state_from_json,
    migrate_session_state_active_polls,
    migrate_session_state_mappings,
)
from config.v2_settings import SettingsState, load_settings_state_from_json
from storage.db import create_sqlite_engine
from storage.lock import MigrationFileLock
from storage.migrations import run_migrations
from storage.models import agent_sessions, auth_codes, imported_state_tables, runtime_records, scope_settings, scopes, state_meta
from storage.sessions_service import SESSIONS_LAST_ACTIVITY_KEY, SQLiteSessionsService
from storage.settings_service import SQLiteSettingsService, upsert_scope

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
    primary_platform: str | None = None,
) -> MigrationImportReport:
    """Create/migrate the SQLite DB and import existing JSON state once."""

    target_db = (db_path or paths.get_sqlite_state_path()).expanduser().resolve()
    target_state_dir = (state_dir or paths.get_state_dir()).expanduser().resolve()
    _ensure_sqlite_target_dirs(
        target_state_dir=target_state_dir,
        target_db=target_db,
        use_default_dirs=db_path is None and state_dir is None,
    )
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

                _clear_imported_state(conn)

            backup_path = _backup_json_state(target_state_dir)
            parsed = _parse_json_state(target_state_dir, primary_platform=primary_platform)
            _write_parsed_state(target_db, parsed)

            with engine.begin() as conn:
                discovered_count = _import_discovered_chats(conn, parsed.discovered)
                counts = _current_counts(conn)
                counts["discovered_scopes"] = discovered_count
                _validate_import(conn, counts)
                _set_import_marker(conn)
                return MigrationImportReport(
                    db_path=target_db,
                    imported=True,
                    backup_path=backup_path,
                    counts=_current_counts(conn) | {"discovered_scopes": discovered_count},
                )
        finally:
            engine.dispose()


def _ensure_sqlite_target_dirs(*, target_state_dir: Path, target_db: Path, use_default_dirs: bool) -> None:
    if use_default_dirs:
        paths.ensure_data_dirs()
        return
    target_state_dir.mkdir(parents=True, exist_ok=True)
    target_db.parent.mkdir(parents=True, exist_ok=True)


def resolve_primary_platform_from_config(state_dir: Path | None = None) -> str | None:
    """Best-effort primary platform lookup for store-level SQLite bootstrap."""
    config_paths = _candidate_config_paths(state_dir)
    for config_path in config_paths:
        platform = _resolve_primary_platform_from_config_path(config_path)
        if platform is not None:
            return platform
    return None


def _candidate_config_paths(state_dir: Path | None) -> list[Path]:
    if state_dir is None:
        return [paths.get_config_path()]

    state_path = Path(state_dir).expanduser().resolve()
    candidates: list[Path] = []
    if state_path.name == "state":
        candidates.append(state_path.parent / "config" / "config.json")
    candidates.append(state_path / "config.json")
    return candidates


def _resolve_primary_platform_from_config_path(config_path: Path) -> str | None:
    try:
        if not config_path.exists():
            return None
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    platforms = payload.get("platforms")
    if isinstance(platforms, dict):
        primary = platforms.get("primary")
        if isinstance(primary, str) and primary.strip():
            return primary.strip()

    platform = payload.get("platform")
    if isinstance(platform, str) and platform.strip():
        return platform.strip()
    return None


def _has_import_marker(conn: Connection) -> bool:
    return (
        conn.execute(select(state_meta.c.value_json).where(state_meta.c.key == JSON_IMPORT_MARKER)).scalar_one_or_none()
        is not None
    )


def _set_import_marker(conn: Connection) -> None:
    now = _utc_now_iso()
    conn.execute(
        state_meta.insert().values(
            key=JSON_IMPORT_MARKER,
            value_json=_json_dumps(now),
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
    sessions: SessionState
    discovered: DiscoveredChatsStore


def _parse_json_state(state_dir: Path, *, primary_platform: str | None) -> _ParsedState:
    settings = _load_settings_from_copy(state_dir / "settings.json")
    sessions = _load_sessions_from_copy(state_dir / "sessions.json", primary_platform=primary_platform)
    discovered = _load_discovered_chats_strict(state_dir / "discovered_chats.json")
    return _ParsedState(settings=settings, sessions=sessions, discovered=discovered)


def _load_settings_from_copy(source: Path) -> SettingsState:
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "settings.json"
        if source.exists():
            shutil.copy2(source, target)
        state, _migrated = load_settings_state_from_json(target)
        return state


def _load_sessions_from_copy(source: Path, *, primary_platform: str | None) -> SessionState:
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "sessions.json"
        if source.exists():
            shutil.copy2(source, target)
        state = load_session_state_from_json(target)
        _migrate_session_state_for_import(state, primary_platform=primary_platform)
        return state


def _load_discovered_chats_strict(source: Path) -> DiscoveredChatsStore:
    if source.exists():
        payload = json.loads(source.read_text(encoding="utf-8"))
        _validate_discovered_chats_payload(payload)
    return DiscoveredChatsStore(source)


def _validate_discovered_chats_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("discovered_chats.json must contain a JSON object")

    raw_platforms = payload.get("platforms", {})
    if raw_platforms is None:
        return
    if not isinstance(raw_platforms, dict):
        raise ValueError("discovered_chats.json platforms must contain a JSON object")

    for platform, chats in raw_platforms.items():
        if not isinstance(chats, dict):
            raise ValueError(f"discovered_chats.json platform {platform!r} must contain a JSON object")
        for chat_id, chat_payload in chats.items():
            if not isinstance(chat_payload, dict):
                raise ValueError(
                    f"discovered_chats.json chat {platform!r}/{chat_id!r} must contain a JSON object"
                )


def _migrate_session_state_for_import(state: SessionState, *, primary_platform: str | None) -> None:
    if primary_platform is not None and not primary_platform.strip():
        raise ValueError("primary_platform must be non-empty when provided")

    needs_default_platform = False
    for data in state.active_polls.values():
        if not isinstance(data, dict) or data.get("platform"):
            continue
        settings_key = data.get("settings_key", "")
        if not isinstance(settings_key, str) or "::" not in settings_key or not settings_key.split("::", 1)[0]:
            needs_default_platform = True
            break

    if not needs_default_platform:
        for scope_key, agent_maps in state.session_mappings.items():
            if "::" in str(scope_key) or not agent_maps:
                continue
            if not infer_platform_from_thread_ids(agent_maps):
                needs_default_platform = True
                break

    if needs_default_platform and primary_platform is None:
        raise ValueError(
            "primary_platform is required to import legacy sessions.json entries that do not encode a platform"
        )

    default_platform = primary_platform or ""
    migrate_session_state_active_polls(state, default_platform)
    migrate_session_state_mappings(state, default_platform)


def _clear_imported_state(conn: Connection) -> None:
    for table in imported_state_tables:
        conn.execute(table.delete())
    conn.execute(state_meta.delete().where(state_meta.c.key == JSON_IMPORT_MARKER))
    conn.execute(state_meta.delete().where(state_meta.c.key == SESSIONS_LAST_ACTIVITY_KEY))


def _write_parsed_state(db_path: Path, parsed: _ParsedState) -> None:
    settings_service = SQLiteSettingsService(db_path)
    sessions_service = SQLiteSessionsService(db_path)
    try:
        settings_service.save_state(parsed.settings)
        sessions_service.save_state(parsed.sessions)
    finally:
        settings_service.close()
        sessions_service.close()


def _import_discovered_chats(conn: Connection, discovered: DiscoveredChatsStore) -> int:
    now = _utc_now_iso()
    count = 0
    for platform, chats in discovered.state.chats.items():
        for chat_id, chat in chats.items():
            upsert_scope(
                conn,
                str(platform),
                "channel",
                str(chat_id),
                display_name=chat.name,
                native_type=chat.chat_type,
                is_private=chat.is_private,
                supports_threads=chat.supports_topics,
                metadata={
                    "username": chat.username,
                    "is_forum": chat.is_forum,
                    "last_seen_at": chat.last_seen_at,
                },
                now=now,
            )
            count += 1
    return count


def _validate_import(conn: Connection, _counts: dict[str, int]) -> None:
    integrity = conn.exec_driver_sql("PRAGMA integrity_check").scalar_one()
    if integrity != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {integrity}")


def _current_counts(conn: Connection) -> dict[str, int]:
    tables = {
        "scopes": scopes,
        "scope_settings": scope_settings,
        "auth_codes": auth_codes,
        "agent_sessions": agent_sessions,
        "runtime_records": runtime_records,
    }
    return {key: int(conn.execute(select(func.count()).select_from(table)).scalar_one()) for key, table in tables.items()}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
