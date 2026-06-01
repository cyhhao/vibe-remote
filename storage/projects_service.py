"""CRUD service for avibe projects.

Projects are first-class entities in the workbench but reuse the
existing ``scopes`` + ``scope_settings`` tables instead of introducing
a parallel store: an avibe project is just a scope row with
``platform='avibe'`` and ``scope_type='project'``. The local folder
path lives in ``scope_settings.workdir`` so Agent runs can pick it up
without a second lookup, and "archived" is modelled as
``scope_settings.enabled = 0`` for parity with how other scopes get
disabled.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from storage.models import scope_settings, scopes


PROJECT_PLATFORM = "avibe"
PROJECT_SCOPE_TYPE = "project"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_project_id() -> str:
    # 12 hex chars = 48 bits → plenty for a single-user installation, and
    # the prefix keeps the id self-documenting in logs / UI tooltips.
    return f"proj_{uuid.uuid4().hex[:12]}"


def _make_scope_id(native_id: str) -> str:
    # Mirrors ``storage.settings_service.make_scope_id`` — kept inline so
    # this module stays decoupled from the broader settings surface.
    return f"{PROJECT_PLATFORM}::{PROJECT_SCOPE_TYPE}::{native_id}"


def _resolve_folder(folder_path: str) -> Path:
    folder = Path(folder_path).expanduser().resolve()
    if not folder.exists():
        raise FileNotFoundError(f"Folder does not exist: {folder}")
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a directory: {folder}")
    return folder


def _find_project_by_workdir(conn: Connection, workdir: str) -> Optional[dict[str, Any]]:
    """Find an existing avibe *project* scope whose folder matches ``workdir``.

    Only avibe project scopes are considered: IM channel scopes can carry
    their own ``scope_settings.workdir``, so matching across all scopes would
    wrongly collide a project with a chat channel that happens to share a cwd.
    ``workdir`` must already be a resolved absolute path (see ``_resolve_folder``)
    so it lines up with how projects are stored. When legacy duplicates share a
    path, prefer an active row, then the most recently seen, so the pick is
    deterministic.
    """

    row = (
        conn.execute(
            select(
                scopes.c.id.label("scope_id"),
                scopes.c.native_id,
                scope_settings.c.enabled,
            )
            .select_from(scopes.join(scope_settings, scope_settings.c.scope_id == scopes.c.id))
            .where(
                scopes.c.platform == PROJECT_PLATFORM,
                scopes.c.scope_type == PROJECT_SCOPE_TYPE,
                scope_settings.c.workdir == workdir,
            )
            .order_by(
                scope_settings.c.enabled.desc(),
                scopes.c.last_seen_at.desc(),
                scopes.c.id.asc(),
            )
            .limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return {"scope_id": row["scope_id"], "native_id": row["native_id"], "enabled": row["enabled"]}


def list_projects(conn: Connection, *, include_archived: bool = False) -> list[dict[str, Any]]:
    """Return all avibe projects sorted by recency, optionally including archived ones."""

    query = (
        select(
            scopes.c.id.label("scope_id"),
            scopes.c.native_id,
            scopes.c.display_name,
            scopes.c.metadata_json,
            scopes.c.first_seen_at,
            scopes.c.last_seen_at,
            scope_settings.c.enabled,
            scope_settings.c.workdir,
            scope_settings.c.updated_at.label("settings_updated_at"),
        )
        .select_from(scopes.outerjoin(scope_settings, scope_settings.c.scope_id == scopes.c.id))
        .where(scopes.c.platform == PROJECT_PLATFORM, scopes.c.scope_type == PROJECT_SCOPE_TYPE)
        .order_by(scopes.c.last_seen_at.desc())
    )
    rows = conn.execute(query).mappings().all()
    out: list[dict[str, Any]] = []
    for row in rows:
        enabled = bool(row["enabled"]) if row["enabled"] is not None else True
        if not include_archived and not enabled:
            continue
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        out.append({
            "id": row["native_id"],
            "scope_id": row["scope_id"],
            "display_name": row["display_name"] or row["native_id"],
            "folder_path": row["workdir"] or "",
            "created_at": row["first_seen_at"],
            "last_active_at": row["last_seen_at"],
            "archived": not enabled,
            "metadata": metadata,
        })
    return out


def get_project(conn: Connection, project_id: str) -> dict[str, Any]:
    scope_id = _make_scope_id(project_id)
    return _project_payload(conn, scope_id)


def create_project(
    conn: Connection,
    folder_path: str,
    display_name: Optional[str] = None,
) -> dict[str, Any]:
    """Create an avibe project, or reuse the existing one for this folder.

    Projects are keyed by their resolved absolute folder path. Opening or
    creating a project that points at a folder we already track returns the
    existing project instead of minting a duplicate scope, and an archived
    match is revived (``enabled = 1``) — this is how a project is restored
    after archiving, without a dedicated unarchive endpoint. The caller's
    ``display_name`` is intentionally ignored on reuse so re-opening a folder
    never clobbers a name the user set earlier; renaming stays explicit.
    """

    folder = _resolve_folder(folder_path)
    now = _utc_now_iso()

    existing = _find_project_by_workdir(conn, str(folder))
    if existing is not None:
        scope_id = existing["scope_id"]
        if not existing["enabled"]:
            conn.execute(
                update(scope_settings)
                .where(scope_settings.c.scope_id == scope_id)
                .values(enabled=1, updated_at=now)
            )
        # Treat (re)opening as recent activity so the project sorts to the top.
        conn.execute(
            update(scopes)
            .where(scopes.c.id == scope_id)
            .values(last_seen_at=now, updated_at=now)
        )
        return _project_payload(conn, scope_id)

    project_id = _new_project_id()
    scope_id = _make_scope_id(project_id)
    name = (display_name or folder.name).strip() or project_id

    conn.execute(
        scopes.insert().values(
            id=scope_id,
            platform=PROJECT_PLATFORM,
            scope_type=PROJECT_SCOPE_TYPE,
            native_id=project_id,
            parent_scope_id=None,
            display_name=name,
            native_type="project",
            is_private=1,
            supports_threads=1,
            metadata_json=json.dumps({}),
            first_seen_at=now,
            last_seen_at=now,
            updated_at=now,
        )
    )
    conn.execute(
        scope_settings.insert().values(
            scope_id=scope_id,
            enabled=1,
            role=None,
            workdir=str(folder),
            agent_name=None,
            agent_backend=None,
            agent_variant=None,
            model=None,
            reasoning_effort=None,
            require_mention=None,
            settings_version=1,
            settings_json=json.dumps({}),
            created_at=now,
            updated_at=now,
        )
    )
    return _project_payload(conn, scope_id)


def update_project(
    conn: Connection,
    project_id: str,
    *,
    display_name: Optional[str] = None,
    folder_path: Optional[str] = None,
) -> dict[str, Any]:
    scope_id = _make_scope_id(project_id)
    existing = conn.execute(select(scopes.c.id).where(scopes.c.id == scope_id)).scalar_one_or_none()
    if existing is None:
        raise LookupError(f"Project not found: {project_id}")

    now = _utc_now_iso()
    if display_name is not None:
        cleaned = display_name.strip()
        if cleaned:
            conn.execute(
                update(scopes)
                .where(scopes.c.id == scope_id)
                .values(display_name=cleaned, updated_at=now, last_seen_at=now)
            )
    if folder_path is not None:
        folder = _resolve_folder(folder_path)
        conn.execute(
            update(scope_settings)
            .where(scope_settings.c.scope_id == scope_id)
            .values(workdir=str(folder), updated_at=now)
        )

    return _project_payload(conn, scope_id)


def archive_project(conn: Connection, project_id: str) -> dict[str, Any]:
    scope_id = _make_scope_id(project_id)
    existing = conn.execute(select(scopes.c.id).where(scopes.c.id == scope_id)).scalar_one_or_none()
    if existing is None:
        raise LookupError(f"Project not found: {project_id}")

    now = _utc_now_iso()
    conn.execute(
        update(scope_settings)
        .where(scope_settings.c.scope_id == scope_id)
        .values(enabled=0, updated_at=now)
    )
    conn.execute(
        update(scopes)
        .where(scopes.c.id == scope_id)
        .values(updated_at=now)
    )
    return _project_payload(conn, scope_id)


def _project_payload(conn: Connection, scope_id: str) -> dict[str, Any]:
    row = conn.execute(
        select(
            scopes.c.id.label("scope_id"),
            scopes.c.native_id,
            scopes.c.display_name,
            scopes.c.metadata_json,
            scopes.c.first_seen_at,
            scopes.c.last_seen_at,
            scope_settings.c.enabled,
            scope_settings.c.workdir,
        )
        .select_from(scopes.outerjoin(scope_settings, scope_settings.c.scope_id == scopes.c.id))
        .where(scopes.c.id == scope_id)
    ).mappings().first()
    if row is None:
        raise LookupError(f"Project not found: {scope_id}")
    enabled = bool(row["enabled"]) if row["enabled"] is not None else True
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return {
        "id": row["native_id"],
        "scope_id": row["scope_id"],
        "display_name": row["display_name"] or row["native_id"],
        "folder_path": row["workdir"] or "",
        "created_at": row["first_seen_at"],
        "last_active_at": row["last_seen_at"],
        "archived": not enabled,
        "metadata": metadata,
    }


def make_directory(path: str) -> str:
    """Create a directory (with parents) and return its absolute path.

    Mirrors the folder picker's expectation that mkdir errors when the
    target already exists — that keeps the UI from silently overwriting
    a different folder when the user supplies an existing name.
    """

    folder = Path(path).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=False)
    return str(folder)
