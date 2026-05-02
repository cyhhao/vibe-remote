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
    _stamp_existing_initial_schema(target_db, cfg)
    command.upgrade(cfg, revision)


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
            if version is not None:
                return
        if not INITIAL_TABLES.issubset(tables):
            return

    command.stamp(cfg, INITIAL_REVISION)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "select name from sqlite_master where type = 'table'",
        )
    }
