from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from config import paths
from storage.db import sqlite_url

INITIAL_REVISION = "20260501_0001"
INITIAL_TABLES = {
    "schema_meta",
    "scopes",
    "channel_settings",
    "guild_settings",
    "guild_policies",
    "user_settings",
    "bind_codes",
    "agent_session_bindings",
    "active_threads",
    "active_polls",
    "processed_messages",
    "chat_sessions",
    "session_messages",
    "discovered_chats",
}


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
    _stamp_existing_initial_schema(target_db, cfg)
    command.upgrade(cfg, revision)


def _stamp_existing_initial_schema(db_path: Path, cfg: Config) -> None:
    path = db_path.expanduser().resolve()
    if not path.exists():
        return

    with sqlite3.connect(path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'",
            )
        }
        if not tables:
            return
        if "alembic_version" in tables:
            version = conn.execute("select version_num from alembic_version").fetchone()
            if version is not None:
                return
        if not INITIAL_TABLES.issubset(tables):
            return

    command.stamp(cfg, INITIAL_REVISION)
