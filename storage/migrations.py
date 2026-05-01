from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from config import paths
from storage.db import sqlite_url


def alembic_dir() -> Path:
    return Path(__file__).resolve().parent / "alembic"


def alembic_config(db_path: Path | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(alembic_dir()))
    cfg.set_main_option("sqlalchemy.url", sqlite_url(db_path or paths.get_sqlite_state_path()))
    return cfg


def run_migrations(db_path: Path | None = None, *, revision: str = "head") -> None:
    command.upgrade(alembic_config(db_path), revision)
