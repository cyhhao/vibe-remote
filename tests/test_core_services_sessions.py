"""Contract tests for ``core.services.sessions``.

This module is the public business API for the ``agent_sessions`` table.
The tests here pin the shape so callers (UI server, CLI, IM adapter)
can rely on it across refactors. Any change that breaks the row payload
shape or the public function set must update this file in lock-step.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.services import sessions as sessions_service
from storage import workbench_sessions_service as storage_sessions
from storage.db import create_sqlite_engine
from storage.importer import ensure_sqlite_state
from storage.settings_service import upsert_scope


@pytest.fixture()
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    yield tmp_path


def _seed_avibe_scope(conn) -> str:
    return upsert_scope(
        conn,
        platform="avibe",
        scope_type="project",
        native_id="proj_contract",
        now="2026-05-26T13:00:00Z",
    )


# --- Public surface ---------------------------------------------------


def test_public_surface_is_stable():
    """The service module's ``__all__`` is the locked public API."""
    expected = {
        # Modern workbench CRUD (takes ``conn``):
        "archive_session",
        "create_session",
        "get_session",
        "list_sessions",
        "touch_session",
        "update_session",
        # Legacy IM-style reservation helpers added in C2 for the CLI:
        "reserve_agent_session",
        "reserve_private_agent_session",
        # Backend-pin guard raised by update_session on a cross-backend switch:
        "SessionBackendLockedError",
    }
    assert set(sessions_service.__all__) == expected
    for name in expected:
        assert callable(getattr(sessions_service, name))


def test_each_workbench_function_delegates_to_storage():
    """The conn-based workbench CRUD functions are thin re-exports of the
    storage module. The C2 reservation helpers wrap a different storage
    class (engine-owning) so they are not part of this delegation check.
    """
    for name in (
        "archive_session",
        "create_session",
        "get_session",
        "list_sessions",
        "touch_session",
        "update_session",
    ):
        assert getattr(sessions_service, name) is getattr(storage_sessions, name)


# --- Round-trip via the public API ------------------------------------


def test_create_and_get_round_trip(isolated_state):
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_avibe_scope(conn)
        created = sessions_service.create_session(
            conn,
            scope_id=scope_id,
            agent_backend="claude",
            agent_name="contract-bot",
        )

    assert created["scope_id"] == scope_id
    assert created["agent_backend"] == "claude"
    assert created["agent_name"] == "contract-bot"

    with engine.connect() as conn:
        fetched = sessions_service.get_session(conn, created["id"])
    assert fetched["id"] == created["id"]
    assert fetched["agent_name"] == "contract-bot"


def test_update_then_list_reflects_changes(isolated_state):
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_avibe_scope(conn)
        session = sessions_service.create_session(
            conn,
            scope_id=scope_id,
            agent_backend="claude",
        )
        sessions_service.update_session(
            conn,
            session["id"],
            title="renamed",
            model="claude-sonnet-4-6",
        )

    with engine.connect() as conn:
        page = sessions_service.list_sessions(conn, scope_id=scope_id)
    assert len(page["sessions"]) == 1
    assert page["sessions"][0]["title"] == "renamed"
    assert page["sessions"][0]["model"] == "claude-sonnet-4-6"


def test_archive_marks_session(isolated_state):
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_avibe_scope(conn)
        session = sessions_service.create_session(
            conn,
            scope_id=scope_id,
            agent_backend="claude",
        )
        archived = sessions_service.archive_session(conn, session["id"])

    assert archived["status"] == "archived"

    with engine.connect() as conn:
        page = sessions_service.list_sessions(conn, scope_id=scope_id, status="active")
    assert page["sessions"] == [], "archived sessions should not appear in the active list"


def test_update_session_present_null_clears_model_and_effort(isolated_state):
    """Switching to an agent with no default model/effort sends present nulls;
    update_session must CLEAR the columns (drop the prior agent's override),
    while omitting the fields leaves them untouched (Codex P2)."""
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_avibe_scope(conn)
        session = sessions_service.create_session(
            conn, scope_id=scope_id, agent_backend="codex", model="gpt-5-codex", reasoning_effort="high"
        )
        sid = session["id"]
        # Present null → clear both.
        sessions_service.update_session(conn, sid, model=None, reasoning_effort=None)
    with engine.connect() as conn:
        cleared = sessions_service.get_session(conn, sid)
    assert cleared["model"] is None
    assert cleared["reasoning_effort"] is None

    # Omitting the fields leaves the (re-set) values untouched.
    with engine.begin() as conn:
        sessions_service.update_session(conn, sid, model="claude-sonnet-4-6", reasoning_effort="low")
        sessions_service.update_session(conn, sid, title="renamed")  # model/effort omitted
    with engine.connect() as conn:
        kept = sessions_service.get_session(conn, sid)
    assert kept["model"] == "claude-sonnet-4-6"
    assert kept["reasoning_effort"] == "low"
    assert kept["title"] == "renamed"


def test_update_session_pins_backend_after_native_bound(isolated_state):
    """A session is locked to its backend once it has a native conversation:
    same-backend agent/model changes are allowed; a cross-backend switch raises
    SessionBackendLockedError. A session with no native yet can still switch
    freely (the avibe header picks the backend before the first turn)."""
    from sqlalchemy import update as sa_update

    from storage.models import agent_sessions

    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_avibe_scope(conn)
        sid = sessions_service.create_session(
            conn, scope_id=scope_id, agent_backend="claude", agent_name="claude"
        )["id"]
        # No native yet → switching backend is allowed.
        sessions_service.update_session(conn, sid, agent_backend="codex", agent_name="codex")
        # Bind a native → backend is now pinned.
        conn.execute(
            sa_update(agent_sessions).where(agent_sessions.c.id == sid).values(native_session_id="nat-1")
        )
        # Same-backend change (different agent / model) is still allowed.
        sessions_service.update_session(conn, sid, agent_backend="codex", agent_name="codex-pro", model="o3")
        # Cross-backend switch is rejected.
        with pytest.raises(sessions_service.SessionBackendLockedError):
            sessions_service.update_session(conn, sid, agent_backend="claude", agent_name="claude")


def test_update_session_blank_backend_takes_first_concrete_pin(isolated_state):
    """A plain Workbench chat is created with an EMPTY agent_backend. After its
    first turn binds a native, selecting the real agent in the chat header is the
    INITIAL pin, not a cross-backend switch — it must be allowed, then lock the
    session to that backend going forward (Codex P2: otherwise the chat can't pick
    an agent/model after its first reply)."""
    from sqlalchemy import update as sa_update

    from storage.models import agent_sessions

    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = _seed_avibe_scope(conn)
        sid = sessions_service.create_session(conn, scope_id=scope_id, agent_backend="")["id"]
        conn.execute(
            sa_update(agent_sessions).where(agent_sessions.c.id == sid).values(native_session_id="nat-blank")
        )
        # Empty -> concrete is the first pin, allowed even with a native bound.
        sessions_service.update_session(conn, sid, agent_backend="codex", agent_name="codex")
        assert sessions_service.get_session(conn, sid)["agent_backend"] == "codex"
        # Now pinned: a different backend is rejected.
        with pytest.raises(sessions_service.SessionBackendLockedError):
            sessions_service.update_session(conn, sid, agent_backend="claude", agent_name="claude")
