"""Tests for avibe project find-or-create / archive-recovery semantics.

Projects are keyed by their resolved absolute folder path. Creating or
"opening" a project for a folder we already track must reuse the existing
scope (no duplicates) and revive it if it was archived — that is how a
project is restored after archiving, without a dedicated unarchive endpoint.
"""

from __future__ import annotations

import pytest

from storage import projects_service
from storage.db import create_sqlite_engine
from storage.importer import ensure_sqlite_state
from storage.models import scope_settings, scopes


@pytest.fixture
def engine():
    # conftest's autouse fixture points VIBE_REMOTE_HOME at a per-test tmp dir,
    # so this initialises and connects to an isolated SQLite state file.
    ensure_sqlite_state()
    return create_sqlite_engine()


def test_create_project_is_idempotent_by_path(engine, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    resolved = str(folder.resolve())

    with engine.begin() as conn:
        first = projects_service.create_project(conn, str(folder), display_name="My Project")
    with engine.begin() as conn:
        second = projects_service.create_project(conn, str(folder), display_name="Different Name")
        projects = projects_service.list_projects(conn, include_archived=True)

    # Same scope reused, not a fresh duplicate.
    assert first["id"] == second["id"]
    assert first["scope_id"] == second["scope_id"]
    # Reuse keeps the original name; the second call's display_name is ignored.
    assert second["display_name"] == "My Project"
    # Exactly one project tracks this path.
    same_path = [p for p in projects if p["folder_path"] == resolved]
    assert len(same_path) == 1


def test_reopening_archived_path_revives_it(engine, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()

    with engine.begin() as conn:
        created = projects_service.create_project(conn, str(folder), display_name="Kept Name")
        projects_service.archive_project(conn, created["id"])

    # Archived projects drop out of the default list.
    with engine.connect() as conn:
        assert all(p["id"] != created["id"] for p in projects_service.list_projects(conn))

    # Re-opening the same folder restores the same project (revived, name kept).
    with engine.begin() as conn:
        revived = projects_service.create_project(conn, str(folder), display_name="Ignored On Reuse")

    assert revived["id"] == created["id"]
    assert revived["archived"] is False
    assert revived["display_name"] == "Kept Name"
    with engine.connect() as conn:
        assert any(p["id"] == created["id"] for p in projects_service.list_projects(conn))


def test_different_paths_make_distinct_projects(engine, tmp_path):
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()

    with engine.begin() as conn:
        a = projects_service.create_project(conn, str(a_dir))
        b = projects_service.create_project(conn, str(b_dir))

    assert a["id"] != b["id"]
    assert a["scope_id"] != b["scope_id"]


def test_path_lookup_ignores_non_project_scopes(engine, tmp_path):
    """An IM-channel scope sharing the same workdir must never be matched."""
    folder = tmp_path / "shared"
    folder.mkdir()
    workdir = str(folder.resolve())

    # Seed a Slack channel scope pointing at the same directory.
    with engine.begin() as conn:
        ts = "2026-01-01T00:00:00Z"
        conn.execute(
            scopes.insert().values(
                id="slack::channel::C1",
                platform="slack",
                scope_type="channel",
                native_id="C1",
                parent_scope_id=None,
                display_name="chan",
                native_type="channel",
                is_private=0,
                supports_threads=1,
                metadata_json="{}",
                first_seen_at=ts,
                last_seen_at=ts,
                updated_at=ts,
            )
        )
        conn.execute(
            scope_settings.insert().values(
                scope_id="slack::channel::C1",
                enabled=1,
                role=None,
                workdir=workdir,
                agent_name=None,
                agent_backend=None,
                agent_variant=None,
                model=None,
                reasoning_effort=None,
                require_mention=None,
                settings_version=1,
                settings_json="{}",
                created_at=ts,
                updated_at=ts,
            )
        )

    with engine.begin() as conn:
        # The channel sharing the path is not a project match...
        assert projects_service._find_project_by_workdir(conn, workdir) is None
        # ...so creating a project for it mints a real avibe project scope.
        proj = projects_service.create_project(conn, workdir)

    assert proj["scope_id"].startswith("avibe::project::")


def test_duplicate_path_pick_prefers_active_then_recent(engine, tmp_path):
    """Legacy duplicates for one path resolve deterministically: active first."""
    folder = tmp_path / "dup"
    folder.mkdir()
    workdir = str(folder.resolve())

    def _insert_project(scope_id: str, *, enabled: int, last_seen: str) -> None:
        with engine.begin() as conn:
            conn.execute(
                scopes.insert().values(
                    id=scope_id,
                    platform="avibe",
                    scope_type="project",
                    native_id=scope_id.split("::")[-1],
                    parent_scope_id=None,
                    display_name="dup",
                    native_type="project",
                    is_private=1,
                    supports_threads=1,
                    metadata_json="{}",
                    first_seen_at="2026-01-01T00:00:00Z",
                    last_seen_at=last_seen,
                    updated_at=last_seen,
                )
            )
            conn.execute(
                scope_settings.insert().values(
                    scope_id=scope_id,
                    enabled=enabled,
                    role=None,
                    workdir=workdir,
                    agent_name=None,
                    agent_backend=None,
                    agent_variant=None,
                    model=None,
                    reasoning_effort=None,
                    require_mention=None,
                    settings_version=1,
                    settings_json="{}",
                    created_at="2026-01-01T00:00:00Z",
                    updated_at=last_seen,
                )
            )

    # An archived row seen more recently, and an active row seen earlier.
    _insert_project("avibe::project::proj_archived", enabled=0, last_seen="2026-05-02T00:00:00Z")
    _insert_project("avibe::project::proj_active", enabled=1, last_seen="2026-05-01T00:00:00Z")

    with engine.begin() as conn:
        found = projects_service._find_project_by_workdir(conn, workdir)

    # Active wins over the more-recent archived row.
    assert found is not None
    assert found["scope_id"] == "avibe::project::proj_active"
