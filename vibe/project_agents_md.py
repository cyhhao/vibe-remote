"""Read/write a project's ``AGENTS.md`` with a ``CLAUDE.md`` fallback.

The workbench lets a user edit the project-level ``AGENTS.md`` straight from the
Web UI. ``AGENTS.md`` is the canonical agent-instructions file; ``CLAUDE.md`` is
the legacy / Claude-specific name. To keep both tool families working off one
source of truth, the editor offers an optional "migrate ``CLAUDE.md`` into
``AGENTS.md`` and symlink them" toggle:

- **Read** prefers ``AGENTS.md``; when it is missing it falls back to the
  ``CLAUDE.md`` content so an existing project is not shown an empty editor.
- **Save** always writes ``AGENTS.md``. When the symlink toggle is on, it makes
  ``CLAUDE.md`` a symlink pointing at ``AGENTS.md`` — *replacing a real
  ``CLAUDE.md`` file if present*. That is safe because the file's content was
  already read into the editor (and thus written into ``AGENTS.md``) before the
  replacement, so the two are unified rather than losing data.

The functions here are deliberately pure filesystem helpers that operate on an
already-resolved project directory, so the HTTP layer only handles project-id
resolution and the symlink reconciliation stays unit-testable in isolation.
"""

from __future__ import annotations

import os
from pathlib import Path

AGENTS_FILENAME = "AGENTS.md"
CLAUDE_FILENAME = "CLAUDE.md"


def _claude_points_to_agents(claude: Path) -> bool:
    """True when ``CLAUDE.md`` is a symlink that resolves to the sibling
    ``AGENTS.md`` (the state the toggle maintains)."""
    if not claude.is_symlink():
        return False
    try:
        target = os.readlink(claude)
    except OSError:
        return False
    resolved = (claude.parent / target).resolve()
    return resolved == (claude.parent / AGENTS_FILENAME).resolve()


def _is_regular_file(path: Path) -> bool:
    """A real file, i.e. exists and is not a symlink."""
    return path.exists() and not path.is_symlink()


def read_agents_md(project_dir: Path) -> dict:
    """Return the editor seed for ``project_dir``.

    ``source`` is ``"agents"`` / ``"claude"`` / ``"none"`` so the UI can show a
    "loaded from CLAUDE.md" notice when the content came from the fallback.
    """
    agents = project_dir / AGENTS_FILENAME
    claude = project_dir / CLAUDE_FILENAME
    if agents.is_file():
        content, source = agents.read_text(encoding="utf-8"), "agents"
    elif claude.is_file():
        content, source = claude.read_text(encoding="utf-8"), "claude"
    else:
        content, source = "", "none"
    return {
        "content": content,
        "source": source,
        "symlinked": _claude_points_to_agents(claude),
        "claude_is_regular_file": _is_regular_file(claude),
    }


def save_agents_md(project_dir: Path, content: str, symlink: bool) -> dict:
    """Write ``AGENTS.md`` and reconcile the ``CLAUDE.md`` symlink to ``symlink``.

    Invariant: this only ever creates, re-points, or removes a *symlink* named
    ``CLAUDE.md`` — except when ``symlink`` is on, where a real ``CLAUDE.md`` is
    intentionally replaced by the symlink (its content is preserved in
    ``AGENTS.md``, written just above). Toggling off never deletes a real file.
    """
    agents = project_dir / AGENTS_FILENAME
    claude = project_dir / CLAUDE_FILENAME
    agents.write_text(content, encoding="utf-8")

    migrated = False
    symlink_error: str | None = None
    if symlink:
        # Ensure CLAUDE.md -> AGENTS.md; idempotent when already so.
        if not _claude_points_to_agents(claude):
            try:
                if claude.is_symlink() or claude.exists():
                    if _is_regular_file(claude):
                        migrated = True  # replacing a real file (content already in AGENTS.md)
                    claude.unlink()
                os.symlink(AGENTS_FILENAME, claude)  # relative target, sibling file
            except OSError as err:  # e.g. unprivileged Windows symlink
                symlink_error = str(err)
                migrated = False
    else:
        # Only remove a symlink we manage; never delete a real CLAUDE.md.
        if claude.is_symlink():
            try:
                claude.unlink()
            except OSError as err:
                symlink_error = str(err)

    return {
        "symlinked": _claude_points_to_agents(claude),
        "claude_is_regular_file": _is_regular_file(claude),
        "migrated": migrated,
        "symlink_error": symlink_error,
    }
