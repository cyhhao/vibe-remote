from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from config import paths
from storage.db import sqlite_url

INITIAL_REVISION = "20260501_0001"
INITIAL_TABLES = {
    "state_meta",
    "scopes",
    "scope_settings",
    "auth_codes",
    "agent_sessions",
    "runtime_records",
}
HEAD_TABLES = INITIAL_TABLES | {"background_tasks", "background_runs", "show_pages"}
HEAD_REQUIRED_COLUMNS = {
    "background_tasks": {"deleted_at"},
}
UNRELEASED_OLD_INITIAL_TABLES = [
    "session_messages",
    "chat_sessions",
    "channel_settings",
    "guild_settings",
    "guild_policies",
    "user_settings",
    "bind_codes",
    "agent_session_bindings",
    "active_threads",
    "active_polls",
    "processed_messages",
    "discovered_chats",
    "scopes",
    "schema_meta",
    "alembic_version",
]


def alembic_dir() -> Path:
    return Path(__file__).resolve().parent / "alembic"


def alembic_config(db_path: Path | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(alembic_dir()))
    cfg.set_main_option("sqlalchemy.url", sqlite_url(db_path or paths.get_sqlite_state_path()))
    return cfg


def run_migrations(db_path: Path | None = None, *, revision: str = "head") -> None:
    target_db = db_path or paths.get_sqlite_state_path()
    cfg = alembic_config(target_db)
    _reset_unreleased_initial_schema_drift(target_db)
    _repair_unreleased_head_schema_drift(target_db)
    _stamp_existing_initial_schema(target_db, cfg)
    command.upgrade(cfg, revision)


def background_tables_ready(db_path: Path | None = None) -> bool:
    target_db = (db_path or paths.get_sqlite_state_path()).expanduser().resolve()
    if not target_db.exists():
        return False
    with sqlite3.connect(target_db) as conn:
        tables = _table_names(conn)
        return _head_schema_ready(conn, tables)


def initialize_background_tables(db_path: Path | None = None) -> None:
    target_db = db_path or paths.get_sqlite_state_path()
    cfg = alembic_config(target_db)
    command.ensure_version(cfg)
    _repair_unreleased_head_schema_drift(target_db)
    _stamp_existing_initial_schema(target_db, cfg)
    command.upgrade(cfg, "head")


def _reset_unreleased_initial_schema_drift(db_path: Path) -> None:
    path = db_path.expanduser().resolve()
    if not path.exists():
        return

    with sqlite3.connect(path) as conn:
        tables = _table_names(conn)
        if INITIAL_TABLES.issubset(tables):
            return
        if "alembic_version" not in tables:
            return
        version = conn.execute("select version_num from alembic_version").fetchone()
        if version != (INITIAL_REVISION,):
            return
        if not any(table in tables for table in UNRELEASED_OLD_INITIAL_TABLES):
            return

        conn.execute("PRAGMA foreign_keys = OFF")
        for table in UNRELEASED_OLD_INITIAL_TABLES:
            if table in tables:
                conn.execute(f'drop table if exists "{table}"')
        conn.commit()


def _repair_unreleased_head_schema_drift(db_path: Path) -> None:
    path = db_path.expanduser().resolve()
    if not path.exists():
        return

    with sqlite3.connect(path) as conn:
        tables = _table_names(conn)
        if not HEAD_TABLES.issubset(tables):
            return
        if "alembic_version" not in tables:
            return
        version = conn.execute("select version_num from alembic_version").fetchone()
        if version != ("20260515_0002",):
            return

        if _repair_head_required_columns(conn, tables):
            conn.commit()


def _stamp_existing_initial_schema(db_path: Path, cfg: Config) -> None:
    path = db_path.expanduser().resolve()
    if not path.exists():
        return

    with sqlite3.connect(path) as conn:
        tables = _table_names(conn)
        if not tables:
            return
        if "alembic_version" in tables:
            version = conn.execute("select version_num from alembic_version").fetchone()
            if version is not None and version[0]:
                return
        missing_initial_tables = INITIAL_TABLES - tables
        if missing_initial_tables and (tables & INITIAL_TABLES):
            missing = ", ".join(sorted(missing_initial_tables))
            raise RuntimeError(f"existing SQLite schema is incomplete; missing initial tables: {missing}")
        if not INITIAL_TABLES.issubset(tables):
            return
        if HEAD_TABLES.issubset(tables):
            if not _head_schema_ready(conn, tables):
                _repair_head_required_columns(conn, tables)
                conn.commit()
                tables = _table_names(conn)
            if not _head_schema_ready(conn, tables):
                missing = _missing_head_schema_description(conn, tables)
                raise RuntimeError(f"existing SQLite head schema is incomplete; missing: {missing}")
            command.stamp(cfg, "head")
            return

    command.stamp(cfg, INITIAL_REVISION)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "select name from sqlite_master where type = 'table'",
        )
    }


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'pragma table_info("{table}")')}


def _head_schema_ready(conn: sqlite3.Connection, tables: set[str]) -> bool:
    if not HEAD_TABLES.issubset(tables):
        return False
    return all(required_columns.issubset(_column_names(conn, table)) for table, required_columns in HEAD_REQUIRED_COLUMNS.items())


def _repair_head_required_columns(conn: sqlite3.Connection, tables: set[str]) -> bool:
    if not HEAD_TABLES.issubset(tables):
        return False
    changed = False
    existing_columns = _column_names(conn, "background_tasks")
    if "deleted_at" not in existing_columns:
        conn.execute('alter table "background_tasks" add column "deleted_at" VARCHAR')
        changed = True
    return changed


def _missing_head_schema_description(conn: sqlite3.Connection, tables: set[str]) -> str:
    missing_parts = [f"tables {', '.join(sorted(HEAD_TABLES - tables))}"] if not HEAD_TABLES.issubset(tables) else []
    for table, required_columns in HEAD_REQUIRED_COLUMNS.items():
        if table not in tables:
            continue
        missing_columns = required_columns - _column_names(conn, table)
        if missing_columns:
            missing_parts.append(f"{table}.{', '.join(sorted(missing_columns))}")
    return "; ".join(missing_parts) or "unknown head schema drift"
