"""Harness API payload enrichment: resolved session summary + next run.

The web Harness cards show a bound session by title (workbench → linkable to
chat) or by platform + channel name (IM → not linkable), plus a cron task's
next fire time. These are derived server-side in the background store so every
client inherits them. See ``storage/background.py``.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import workbench_sessions_service
from storage.background import SQLiteBackgroundTaskStore, compute_next_run_at
from storage.db import create_sqlite_engine
from storage.models import agent_sessions
from storage.sessions_service import SQLiteSessionsService
from storage.settings_service import upsert_scope

NOW = "2026-05-31T00:00:00Z"


def _build_schema(db_path: Path) -> None:
    # SQLiteSessionsService builds + migrates the core schema (scopes,
    # agent_sessions, ...); the background store later adds run_definitions.
    SQLiteSessionsService(db_path).close()


def test_compute_next_run_at_handles_cron_disabled_and_past() -> None:
    nxt = compute_next_run_at(
        enabled=True, schedule_type="cron", cron="0 9 * * *", run_at=None, timezone_name="Asia/Shanghai"
    )
    assert nxt is not None
    parsed = datetime.fromisoformat(nxt)
    assert parsed.tzinfo is not None  # tz-aware so the client can localize it
    assert parsed > datetime.now(timezone.utc)

    # Disabled tasks and already-fired one-shots have no next run.
    assert compute_next_run_at(
        enabled=False, schedule_type="cron", cron="0 9 * * *", run_at=None, timezone_name="UTC"
    ) is None
    assert compute_next_run_at(
        enabled=True, schedule_type="at", cron=None, run_at="2020-01-01T00:00:00+00:00", timezone_name="UTC"
    ) is None


def test_scheduled_task_payload_resolves_workbench_session(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    _build_schema(db_path)
    engine = create_sqlite_engine(db_path)
    try:
        with engine.begin() as conn:
            scope_id = upsert_scope(
                conn, platform="avibe", scope_type="project", native_id="proj_test", now=NOW
            )
            session = workbench_sessions_service.create_session(
                conn, scope_id=scope_id, agent_backend="claude", agent_name="default"
            )
            conn.execute(
                update(agent_sessions).where(agent_sessions.c.id == session["id"]).values(title="重构鉴权模块")
            )
    finally:
        engine.dispose()

    store = SQLiteBackgroundTaskStore(db_path)
    try:
        store.upsert_scheduled_task(
            {
                "id": "task_wb",
                "name": "每日构建巡检",
                "session_id": session["id"],
                "prompt": "hello",
                "schedule_type": "cron",
                "cron": "0 9 * * *",
                "timezone": "Asia/Shanghai",
                "enabled": True,
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
        task = next(t for t in store.list_scheduled_tasks() if t["id"] == "task_wb")
    finally:
        store.close()

    assert task["session_is_workbench"] is True
    assert task["session_platform"] == "avibe"
    assert task["session_scope_kind"] == "project"
    assert task["session_title"] == "重构鉴权模块"
    assert task["session_label"] == "重构鉴权模块"
    assert task["next_run_at"] is not None
    # get_scheduled_task enriches identically to the list path.
    store2 = SQLiteBackgroundTaskStore(db_path)
    try:
        assert store2.get_scheduled_task("task_wb")["session_title"] == "重构鉴权模块"
    finally:
        store2.close()


def test_scheduled_task_payload_resolves_im_channel_name(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    _build_schema(db_path)
    engine = create_sqlite_engine(db_path)
    try:
        with engine.begin() as conn:
            upsert_scope(
                conn,
                platform="slack",
                scope_type="channel",
                native_id="C0123",
                now=NOW,
                display_name="#dev-ops",
            )
    finally:
        engine.dispose()

    store = SQLiteBackgroundTaskStore(db_path)
    try:
        store.upsert_scheduled_task(
            {
                "id": "task_im",
                "name": "周报推送",
                "session_key": "slack::channel::C0123",
                "prompt": "hello",
                "schedule_type": "cron",
                "cron": "0 18 * * 5",
                "timezone": "UTC",
                "enabled": True,
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
        task = next(t for t in store.list_scheduled_tasks() if t["id"] == "task_im")
    finally:
        store.close()

    assert task["session_is_workbench"] is False
    assert task["session_platform"] == "slack"
    assert task["session_scope_kind"] == "channel"
    assert task["session_label"] == "#dev-ops"  # channel display name, not the raw id


def test_watch_payload_resolves_im_channel_name(tmp_path: Path) -> None:
    db_path = tmp_path / "vibe.sqlite"
    _build_schema(db_path)
    engine = create_sqlite_engine(db_path)
    try:
        with engine.begin() as conn:
            upsert_scope(
                conn,
                platform="slack",
                scope_type="channel",
                native_id="C0999",
                now=NOW,
                display_name="#ops",
            )
    finally:
        engine.dispose()

    store = SQLiteBackgroundTaskStore(db_path)
    try:
        store.upsert_watch(
            {
                "id": "watch_im",
                "name": "部署监控",
                "session_key": "slack::channel::C0999",
                "shell_command": "tail -f /var/log/x.log",
                "mode": "forever",
                "enabled": True,
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
        watch = next(w for w in store.list_watches() if w["id"] == "watch_im")
    finally:
        store.close()

    assert watch["session_is_workbench"] is False
    assert watch["session_platform"] == "slack"
    assert watch["session_label"] == "#ops"
