"""Unit tests for vibe.project_agents_md — AGENTS.md read fallback + the
CLAUDE.md symlink reconciliation that backs the workbench editor."""

import os
from pathlib import Path

from vibe.project_agents_md import read_agents_md, save_agents_md


# --- read_agents_md -------------------------------------------------------


def test_read_empty_dir_returns_empty(tmp_path: Path):
    result = read_agents_md(tmp_path)
    assert result == {
        "content": "",
        "source": "none",
        "symlinked": False,
        "claude_is_regular_file": False,
    }


def test_read_falls_back_to_claude(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("from claude", encoding="utf-8")
    result = read_agents_md(tmp_path)
    assert result["content"] == "from claude"
    assert result["source"] == "claude"
    assert result["claude_is_regular_file"] is True
    assert result["symlinked"] is False


def test_read_prefers_agents_over_claude(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("from agents", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("from claude", encoding="utf-8")
    result = read_agents_md(tmp_path)
    assert result["content"] == "from agents"
    assert result["source"] == "agents"


# --- save_agents_md: symlink ON ------------------------------------------


def test_save_creates_symlink_on_empty_dir(tmp_path: Path):
    result = save_agents_md(tmp_path, "hello", symlink=True)
    agents = tmp_path / "AGENTS.md"
    claude = tmp_path / "CLAUDE.md"
    assert agents.read_text(encoding="utf-8") == "hello"
    assert claude.is_symlink()
    assert os.readlink(claude) == "AGENTS.md"  # relative, sibling target
    assert claude.read_text(encoding="utf-8") == "hello"  # resolves to AGENTS.md
    assert result["symlinked"] is True
    assert result["migrated"] is False
    assert result["symlink_error"] is None


def test_save_migrates_real_claude_file(tmp_path: Path):
    # A pre-existing real CLAUDE.md: its content was read into the editor, so
    # saving with symlink on replaces it with a link and reports migrated.
    (tmp_path / "CLAUDE.md").write_text("legacy claude", encoding="utf-8")
    result = save_agents_md(tmp_path, "unified", symlink=True)
    claude = tmp_path / "CLAUDE.md"
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "unified"
    assert claude.is_symlink()
    assert claude.read_text(encoding="utf-8") == "unified"
    assert result["migrated"] is True
    assert result["claude_is_regular_file"] is False


def test_save_symlink_is_idempotent(tmp_path: Path):
    save_agents_md(tmp_path, "v1", symlink=True)
    result = save_agents_md(tmp_path, "v2", symlink=True)
    claude = tmp_path / "CLAUDE.md"
    assert claude.is_symlink()
    assert claude.read_text(encoding="utf-8") == "v2"
    assert result["symlinked"] is True
    assert result["migrated"] is False  # already linked, not a fresh migration


def test_save_repoints_foreign_symlink(tmp_path: Path):
    # CLAUDE.md symlinked to something else gets re-pointed at AGENTS.md.
    (tmp_path / "other.md").write_text("other", encoding="utf-8")
    os.symlink("other.md", tmp_path / "CLAUDE.md")
    result = save_agents_md(tmp_path, "content", symlink=True)
    claude = tmp_path / "CLAUDE.md"
    assert os.readlink(claude) == "AGENTS.md"
    assert result["symlinked"] is True
    assert result["migrated"] is False  # it was a symlink, not a real file


# --- save_agents_md: symlink OFF -----------------------------------------


def test_save_off_removes_managed_symlink(tmp_path: Path):
    save_agents_md(tmp_path, "x", symlink=True)
    assert (tmp_path / "CLAUDE.md").is_symlink()
    result = save_agents_md(tmp_path, "x", symlink=False)
    assert not (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "x"
    assert result["symlinked"] is False


def test_save_off_never_deletes_real_claude(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("keep me", encoding="utf-8")
    result = save_agents_md(tmp_path, "agents body", symlink=False)
    claude = tmp_path / "CLAUDE.md"
    assert claude.is_file() and not claude.is_symlink()
    assert claude.read_text(encoding="utf-8") == "keep me"  # untouched
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "agents body"
    assert result["claude_is_regular_file"] is True
    assert result["symlinked"] is False
