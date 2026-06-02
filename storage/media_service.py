"""CRUD over the ``media_objects`` proxy-token table.

A single write path (:func:`register`) mints an opaque token for a local file
so the workbench can serve it over ``/api/sessions/<id>/media/<token>`` without
ever putting a filesystem path in the URL. Both agent-reply media (rewritten in
``core/workbench_media``) and user uploads register here, so the proxy endpoint
and the UI file card have one shape to read.
"""

from __future__ import annotations

import mimetypes
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.engine import Connection

from storage.models import media_objects


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_token() -> str:
    # URL-safe, unguessable; the token IS the capability to fetch the file.
    return secrets.token_urlsafe(16)


def register(
    conn: Connection,
    *,
    scope_id: str,
    session_id: Optional[str],
    kind: str,
    source: str,
    local_path: str,
    file_name: Optional[str] = None,
    content_type: Optional[str] = None,
    message_id: Optional[str] = None,
) -> str:
    """Register *local_path* under a fresh token and return the token.

    ``content_type`` / ``file_ext`` / ``size_bytes`` are derived from the path
    when not supplied so the proxy response and UI card don't re-compute them.
    """
    path = Path(local_path)
    name = file_name or path.name
    ext = (path.suffix.lower().lstrip(".") or None)
    ctype = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    size: Optional[int] = None
    try:
        if path.is_file():
            size = path.stat().st_size
    except OSError:
        size = None

    token = _new_token()
    conn.execute(
        media_objects.insert().values(
            token=token,
            scope_id=scope_id,
            session_id=session_id,
            message_id=message_id,
            kind=kind,
            source=source,
            local_path=str(local_path),
            file_name=name,
            content_type=ctype,
            file_ext=ext,
            size_bytes=size,
            created_at=_utc_now_iso(),
            expires_at=None,
            revoked_at=None,
        )
    )
    return token


def get_by_token(conn: Connection, token: str) -> Optional[dict[str, Any]]:
    """Return the media row for *token* as a plain dict, or ``None``."""
    if not token:
        return None
    row = conn.execute(select(media_objects).where(media_objects.c.token == token)).mappings().first()
    return dict(row) if row else None
