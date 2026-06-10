"""Snapshot test for ``vibe agent run --json`` output schema.

C2 of the services-layer refactor moves CLI's session reservation off
``storage.sessions_service.SQLiteSessionsService`` and onto
``core.services.sessions``. The CLI's JSON output shape is the public
contract callers (and scheduled tasks, watch hooks, downstream tools)
depend on — Q8 in the design doc commits to keeping it byte-stable
through the refactor.

This test pins the **keys** in the ``agent_run`` envelope. Values like
run ids and session ids are non-deterministic so we check key presence /
types instead of exact strings.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vibe.cli as cli


def _parse_agent_run(argv: list[str]):
    parser = cli.build_parser()
    return parser.parse_args(["agent", "run", *argv])


_EXPECTED_KEYS = {
    "schema_version",
    "ok",
    "kind",
    "accepted",
    "request_type",
    "run_id",
    "execution_id",
    "agent",
    "session_policy",
    "session_id",
    "deliver_key",
    "callback_session_id",
    "async",
    "run",
}

_EXPECTED_RUN_KEYS_QUEUED = {"id", "status", "run_type", "agent_name", "session_id", "callback_session_id"}


def test_agent_run_async_envelope_schema(tmp_path: Path, capsys) -> None:
    """Locks the top-level keys + nested ``run`` keys for the async path
    (the synchronous path adds the resolved result fields after
    ``_wait_for_run_result``, so they're tested via existing wait-flow
    coverage).
    """

    db_path = tmp_path / "state" / "vibe.sqlite"
    agent_store = cli.VibeAgentStore(db_path)
    agent_store.create(name="worker", backend="codex")
    request_store = cli.TaskExecutionStore(tmp_path / "task_requests")
    args = _parse_agent_run(["--agent", "worker", "--async", "--message", "hi"])

    with (
        patch("vibe.cli._agent_store", return_value=agent_store),
        patch("vibe.cli._task_request_store", return_value=request_store),
        patch("vibe.cli.paths.get_sqlite_state_path", return_value=db_path),
        patch("vibe.cli._primary_platform", return_value="slack"),
    ):
        result = cli.cmd_agent_run(args)

    assert result == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out or captured.err)
    # Envelope shape — these are the public keys that downstream tooling
    # parses; any rename / drop is a breaking change.
    assert set(payload.keys()) == _EXPECTED_KEYS, (
        f"agent_run envelope keys drifted: {set(payload.keys()) ^ _EXPECTED_KEYS}"
    )
    assert payload["schema_version"] == 1
    assert payload["ok"] is True
    assert payload["kind"] == "agent_run"
    assert payload["async"] is True
    assert payload["request_type"] == "agent_run"

    run = payload["run"]
    assert set(run.keys()) == _EXPECTED_RUN_KEYS_QUEUED, (
        f"run sub-payload keys drifted: {set(run.keys()) ^ _EXPECTED_RUN_KEYS_QUEUED}"
    )
    assert run["status"] == "queued"
    assert run["run_type"] == "agent_run"
    assert run["agent_name"] == "worker"
    assert run["id"] == payload["run_id"]


def test_agent_run_async_accepts_callback_session_id(tmp_path: Path, capsys) -> None:
    from core.services import sessions as sessions_service
    from storage.db import create_sqlite_engine
    from storage.importer import ensure_sqlite_state
    from storage.models import scope_settings
    from storage.settings_service import upsert_scope

    state_home = tmp_path / "home"
    with patch.dict("os.environ", {"VIBE_REMOTE_HOME": str(state_home)}):
        ensure_sqlite_state()
        db_path = state_home / "state" / "vibe.sqlite"
        engine = create_sqlite_engine(db_path)
        with engine.begin() as conn:
            scope_id = upsert_scope(
                conn,
                platform="avibe",
                scope_type="project",
                native_id="proj_callback",
                now="2026-06-10T00:00:00Z",
            )
            conn.execute(
                scope_settings.insert().values(
                    scope_id=scope_id,
                    enabled=1,
                    role=None,
                    workdir=str(tmp_path),
                    agent_name=None,
                    agent_backend=None,
                    agent_variant=None,
                    model=None,
                    reasoning_effort=None,
                    require_mention=None,
                    settings_version=1,
                    settings_json="{}",
                    created_at="2026-06-10T00:00:00Z",
                    updated_at="2026-06-10T00:00:00Z",
                )
            )
            callback_session = sessions_service.create_session(
                conn,
                scope_id=scope_id,
                agent_backend="codex",
                agent_name="worker",
            )
        agent_store = cli.VibeAgentStore(db_path)
        agent_store.create(name="worker", backend="codex")
        request_store = cli.TaskExecutionStore(tmp_path / "task_requests")
        args = _parse_agent_run(
            [
                "--agent",
                "worker",
                "--async",
                "--callback-session-id",
                callback_session["id"],
                "--message",
                "hi",
            ]
        )

        with (
            patch("vibe.cli._agent_store", return_value=agent_store),
            patch("vibe.cli._task_request_store", return_value=request_store),
            patch("vibe.cli.paths.get_sqlite_state_path", return_value=db_path),
            patch("vibe.cli._primary_platform", return_value="slack"),
        ):
            result = cli.cmd_agent_run(args)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["callback_session_id"] == callback_session["id"]
    assert payload["run"]["callback_session_id"] == callback_session["id"]
    stored = request_store.get_run(payload["run_id"])
    assert stored is not None
    assert stored["callback_session_id"] == callback_session["id"]
    assert stored["callback_status"] == "pending"


def test_agent_run_callback_session_requires_async(capsys) -> None:
    args = _parse_agent_run(["--agent", "worker", "--callback-session-id", "ses1", "--message", "hi"])

    result = cli.cmd_agent_run(args)

    assert result == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out or captured.err)
    assert payload["code"] == "callback_requires_async"
