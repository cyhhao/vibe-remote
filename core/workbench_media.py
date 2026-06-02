"""Rewrite agent-reply ``file://`` links into same-origin media-proxy URLs.

The IM path (``core/message_dispatcher`` + ``core/reply_enhancer``) strips
``file://`` markdown links out of the reply text and uploads the referenced
files to the IM platform. The avibe workbench Chat needs the opposite: keep the
link **in place** in the Markdown but point it at a same-origin proxy URL, so
the browser can render an agent-produced image inline (and a file as a download
card) without ever touching ``file://`` or an attacker-chosen remote host.

We reuse the reply-enhancer's file-link parser (one home for "what a file link
looks like") and, for each link, register the local file under an opaque token
(:func:`storage.media_service.register`) then swap the URL for
``/api/sessions/<session_id>/media/<token>``. The ``!``/``[]`` Markdown shape is
preserved, so the frontend renders images vs files purely from element type.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.engine import Connection

from config.paths import get_attachments_dir
from core.reply_enhancer import _FILE_LINK_RE, _file_uri_to_local_path
from storage import media_service

logger = logging.getLogger(__name__)


def _allowed_media_roots(workdir: str | None, session_id: str) -> list[Path]:
    """Directories an agent reply may legitimately reference: the session's
    working directory (where it produces files), THIS session's own upload dir,
    and the Codex generated-images dir. The shared OS temp dir is deliberately
    NOT included — ``/tmp`` is writable by unrelated tools and holds transient
    secrets, so a prompt-injected ``[x](file:///tmp/secret)`` must not mint a
    token. The uploads root is scoped to ``attachments/avibe/<session_id>`` (not
    the whole attachments tree) so an agent reply can't mint a token for another
    session's / IM channel's upload. Anything outside these roots
    (``~/.vibe_remote/config.json``, ``~/.ssh``, another session, ...) is refused,
    so untrusted output can't exfiltrate files through the media proxy. Agents
    that want to show a produced file write it under their workdir."""
    roots: list[Path] = []

    def _add(value) -> None:
        try:
            roots.append(Path(value).resolve())
        except Exception:
            pass

    _add(get_attachments_dir() / "avibe" / session_id)
    _add(Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "generated_images")
    if workdir:
        _add(workdir)
    return roots


def _safe_resolved_path(path: str, roots: list[Path]) -> str | None:
    """Resolve *path* (following symlinks) and return it as a string only when it
    sits at/inside one of *roots*; otherwise ``None``."""
    try:
        resolved = Path(path).resolve()
    except Exception:
        return None
    for root in roots:
        if resolved == root or root in resolved.parents:
            return str(resolved)
    return None


def rewrite_agent_media(
    conn: Connection, *, scope_id: str, session_id: str, text: str, workdir: str | None = None
) -> str:
    """Return *text* with ``file://`` links rewritten to media-proxy URLs.

    Registers each referenced file in ``media_objects`` (same transaction as the
    caller's message insert). Only paths inside the session's safe roots
    (workdir / temp / uploads / Codex images) are registered — untrusted agent
    output can't mint a token for an arbitrary file such as ``~/.ssh/id_rsa`` or
    ``~/.vibe_remote/config.json``. Non-``file://`` URLs, non-absolute paths, and
    out-of-root paths are left untouched. Best-effort: a registration failure
    leaves that one link as written rather than dropping the reply.
    """
    if not text or "file://" not in text:
        return text

    roots = _allowed_media_roots(workdir, session_id)

    def _replace(match) -> str:
        bang, label, url = match.group(1), match.group(2), match.group(3)
        parsed = urlparse(url)
        if parsed.scheme != "file":
            return match.group(0)
        path = _file_uri_to_local_path(parsed)
        if not os.path.isabs(path):
            logger.warning("workbench_media: skipping non-absolute file link: %s", url)
            return match.group(0)
        safe_path = _safe_resolved_path(path, roots)
        if safe_path is None:
            logger.warning("workbench_media: refusing media outside safe roots: %s", path)
            return match.group(0)
        try:
            token = media_service.register(
                conn,
                scope_id=scope_id,
                session_id=session_id,
                kind="image" if bang == "!" else "file",
                source="agent_reply",
                local_path=safe_path,
                file_name=label or os.path.basename(safe_path),
            )
        except Exception:
            logger.exception("workbench_media: failed to register media for %s", safe_path)
            return match.group(0)
        return f"{bang}[{label}](/api/sessions/{session_id}/media/{token})"

    return _FILE_LINK_RE.sub(_replace, text)


def resolve_attachment_specs(conn: Connection, *, session_id: str, attachments) -> list[dict]:
    """Resolve UI-sent attachment refs (media tokens) to agent-turn file specs.

    The browser only ever holds opaque tokens (never local paths); this maps each
    token back to its on-disk file via ``media_objects``, scoped to the session,
    and returns JSON-friendly ``{name, mimetype, path, size}`` dicts. Shared by
    the send path (→ dispatch payload) and the queue-flush path (→ rebuilt turn)
    so both carry the same uploaded files into the agent turn.
    """
    specs: list[dict] = []
    for attachment in attachments or []:
        if not isinstance(attachment, dict):
            continue
        token = attachment.get("token")
        if not token:
            continue
        row = media_service.get_by_token(conn, token)
        if not row or row.get("session_id") != session_id or row.get("revoked_at"):
            continue
        specs.append(
            {
                "name": row.get("file_name"),
                "mimetype": row.get("content_type"),
                "path": row.get("local_path"),
                "size": row.get("size_bytes"),
            }
        )
    return specs


def file_attachments_from_specs(specs) -> list | None:
    """Build ``FileAttachment`` objects from JSON file specs (already-local web
    uploads — ``{name, mimetype, path, size}``). Returns ``None`` when empty so
    ``MessageContext.files`` stays falsy for text-only turns. Shared by the
    dispatch payload (internal_server) and the queue-flush re-run (session_turns).
    """
    from modules.im.base import FileAttachment

    files = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        path = spec.get("path")
        if not path:
            continue
        files.append(
            FileAttachment(
                name=spec.get("name") or "attachment",
                mimetype=spec.get("mimetype") or "application/octet-stream",
                local_path=path,
                size=spec.get("size"),
            )
        )
    return files or None
