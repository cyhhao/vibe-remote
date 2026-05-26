"""Business API for the ``agent_sessions`` table.

This module is the **only** import path that UI server / CLI / IM adapter
should use to read or write agent-session rows. Today it re-exports the
existing storage-level helpers; later phases will fold the per-caller
duplication (``storage.workbench_sessions_service`` vs
``storage.sessions_service.SQLiteSessionsService``) into a single set of
free functions here.

Why the early re-export shim:

* Lets callers move onto ``core.services.sessions`` immediately (UI server
  in C1, CLI in C2) without forcing a behavior-affecting rewrite in the
  same commit.
* Pins the public surface so future internal refactors don't ripple into
  every consumer.
* The contract tests in ``tests/test_core_services_sessions.py`` lock
  the shape so a future internal change cannot silently drift the API.

Conventions (see workbench-dispatch-architecture.md §6):

* Public functions take a SQLAlchemy ``Connection`` as their first
  argument. Never construct engines here.
* Return shapes are plain ``dict[str, Any]`` payloads (matching the
  existing ``workbench_sessions_service`` style).
* Errors raise ``LookupError`` / ``ValueError`` so callers can map them
  to HTTP status codes or CLI exit codes without leaking SQLAlchemy
  exceptions.
* No side effects. SSE publishes, audit logs, etc. belong in the calling
  layer (REST route, CLI command, controller handler).
"""

from __future__ import annotations

from storage.workbench_sessions_service import (
    archive_session,
    create_session,
    get_session,
    list_sessions,
    touch_session,
    update_session,
)

__all__ = [
    "archive_session",
    "create_session",
    "get_session",
    "list_sessions",
    "touch_session",
    "update_session",
]
