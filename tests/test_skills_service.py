"""Unit tests for core/services/skills.py — the askill CLI shell.

Hermetic: the subprocess boundary (`_run_askill`) is monkeypatched with
canned ``--json`` envelopes, so these run without askill installed and
without touching the network. They pin the command construction (scope /
agent / install flags), the SKILL.md frontmatter enrichment, and the error
paths.
"""

from __future__ import annotations

import asyncio

import pytest

from core.services import skills


def _run(coro):
    return asyncio.run(coro)


class _Recorder:
    """Stand-in for ``_run_askill`` that records args and returns a fixture."""

    def __init__(self, result):
        self.calls: list[dict] = []
        self.result = result

    async def __call__(self, askill_path, args, *, cwd=None, timeout=skills.DEFAULT_TIMEOUT):
        self.calls.append({"path": askill_path, "args": list(args), "cwd": cwd})
        return self.result


def test_list_global_no_cwd(monkeypatch):
    rec = _Recorder({"ok": True, "skills": []})
    monkeypatch.setattr(skills, "_run_askill", rec)
    out = _run(skills.list_skills("askill", scope="global"))
    assert out == {"ok": True, "skills": []}
    assert rec.calls[0]["args"] == ["list", "-g"]
    assert rec.calls[0]["cwd"] is None


def test_list_project_with_backends_runs_in_cwd(monkeypatch):
    rec = _Recorder({"ok": True, "skills": []})
    monkeypatch.setattr(skills, "_run_askill", rec)
    _run(skills.list_skills("askill", scope="project", project_dir="/p", backends=["claude", "codex"]))
    assert rec.calls[0]["args"] == ["list", "-p", "-a", "claude-code", "-a", "codex"]
    assert rec.calls[0]["cwd"] == "/p"


def test_add_install_flags(monkeypatch):
    rec = _Recorder({"ok": True, "action": "install"})
    monkeypatch.setattr(skills, "_run_askill", rec)
    _run(skills.add_skill("askill", "gh:o/r", scope="global", backends=["opencode"], all_skills=True))
    assert rec.calls[0]["args"] == ["add", "gh:o/r", "-g", "-a", "opencode", "--all", "-y"]
    assert rec.calls[0]["cwd"] is None


def test_add_project_copy_runs_in_cwd(monkeypatch):
    rec = _Recorder({"ok": True, "action": "install"})
    monkeypatch.setattr(skills, "_run_askill", rec)
    _run(skills.add_skill("askill", "./pkg", scope="project", project_dir="/p", copy=True))
    assert rec.calls[0]["args"] == ["add", "./pkg", "-p", "--copy", "-y"]
    assert rec.calls[0]["cwd"] == "/p"


def test_preview_uses_list_flag_without_yes(monkeypatch):
    rec = _Recorder({"ok": True, "action": "preview", "skills": []})
    monkeypatch.setattr(skills, "_run_askill", rec)
    _run(skills.preview_source("askill", "gh:o/r", project_dir="/p"))
    assert rec.calls[0]["args"] == ["add", "gh:o/r", "--list"]
    assert rec.calls[0]["cwd"] == "/p"


def test_remove_flags(monkeypatch):
    rec = _Recorder({"ok": True})
    monkeypatch.setattr(skills, "_run_askill", rec)
    _run(skills.remove_skill("askill", "pdf-tools", scope="project", project_dir="/p", backends=["claude"]))
    assert rec.calls[0]["args"] == ["remove", "pdf-tools", "-p", "-a", "claude-code"]


def test_find_passes_query(monkeypatch):
    rec = _Recorder({"ok": True, "skills": [{"name": "memory"}]})
    monkeypatch.setattr(skills, "_run_askill", rec)
    out = _run(skills.find_skills("askill", "memory"))
    assert rec.calls[0]["args"] == ["find", "memory"]
    assert out["skills"][0]["name"] == "memory"


def test_invalid_backend_raises(monkeypatch):
    monkeypatch.setattr(skills, "_run_askill", _Recorder({"ok": True}))
    with pytest.raises(skills.SkillsError) as info:
        _run(skills.list_skills("askill", scope="all", backends=["bogus"]))
    assert info.value.code == "invalid_backend"


def test_invalid_scope_raises(monkeypatch):
    monkeypatch.setattr(skills, "_run_askill", _Recorder({"ok": True}))
    with pytest.raises(skills.SkillsError) as info:
        _run(skills.add_skill("askill", "gh:o/r", scope="all"))
    assert info.value.code == "invalid_scope"


def test_missing_binary_raises_lookup():
    with pytest.raises(LookupError):
        _run(skills._run_askill("", ["list"]))


def test_list_enriches_from_frontmatter(tmp_path, monkeypatch):
    skill_dir = tmp_path / "pdf-tools"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pdf-tools\ndescription: Do PDF things\nversion: 1.2.0\n---\nbody\n",
        encoding="utf-8",
    )
    rec = _Recorder(
        {"ok": True, "skills": [{"name": "pdf-tools", "scope": "global", "path": str(skill_dir), "agents": []}]}
    )
    monkeypatch.setattr(skills, "_run_askill", rec)
    out = _run(skills.list_skills("askill", scope="global"))
    sk = out["skills"][0]
    assert sk["description"] == "Do PDF things"
    assert sk["version"] == "1.2.0"


def test_enrich_is_noop_without_skill_md(tmp_path, monkeypatch):
    rec = _Recorder(
        {"ok": True, "skills": [{"name": "ghost", "scope": "global", "path": str(tmp_path / "missing"), "agents": []}]}
    )
    monkeypatch.setattr(skills, "_run_askill", rec)
    out = _run(skills.list_skills("askill", scope="global"))
    assert "description" not in out["skills"][0]
