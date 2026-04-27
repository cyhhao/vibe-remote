import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import json

from modules.agents.native_sessions.base import build_resume_preview, build_tail_preview
from modules.agents.native_sessions import claude as claude_module
from modules.agents.native_sessions.claude import ClaudeNativeSessionProvider, encode_project_path
from modules.agents.native_sessions import codex as codex_module
from modules.agents.native_sessions.codex import CodexNativeSessionProvider
from modules.agents.native_sessions import service as service_module
from modules.agents.native_sessions.service import AgentNativeSessionService
from modules.agents.native_sessions.types import NativeResumeSession


def test_claude_provider_falls_back_to_history_jsonl(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    history_path = tmp_path / "history.jsonl"
    projects_root.mkdir(parents=True, exist_ok=True)

    working_path = "/Users/cyh/vibe-remote"
    history_path.write_text(
        "\n".join(
            [
                '{"display":"old prompt","timestamp":1766078000000,"project":"/Users/cyh/vibe-remote","sessionId":"sess_a"}',
                '{"display":"latest prompt","timestamp":1766079000000,"project":"/Users/cyh/vibe-remote","sessionId":"sess_a"}',
                '{"display":"other project","timestamp":1766079100000,"project":"/Users/cyh/other","sessionId":"sess_b"}',
            ]
        ),
        encoding="utf-8",
    )

    provider = ClaudeNativeSessionProvider(root=str(projects_root), history_path=str(history_path))

    items = provider.list_metadata(working_path)

    assert [item.native_session_id for item in items] == ["sess_a"]
    hydrated = provider.hydrate_preview(items[0])
    assert hydrated.last_agent_message == "latest prompt"
    assert hydrated.last_agent_tail == "latest prompt"


def test_claude_project_path_encoding_handles_windows_paths() -> None:
    assert encode_project_path("C:\\Users\\cyh\\vibe-remote") == "C--Users-cyh-vibe-remote"


def test_claude_provider_scans_candidate_jsonl_when_history_has_results(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    history_path = tmp_path / "history.jsonl"
    projects_root.mkdir(parents=True, exist_ok=True)

    working_path = "/Users/cyh/vibe-remote"
    history_path.write_text(
        '{"display":"history prompt","timestamp":1766079000000,"project":"/Users/cyh/vibe-remote","sessionId":"sess_history"}',
        encoding="utf-8",
    )

    candidate_dir = projects_root / encode_project_path(working_path)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "sess_sdk.jsonl").write_text(
        "\n".join(
            [
                '{"type":"user","timestamp":"2026-03-27T09:59:00Z","cwd":"/Users/cyh/vibe-remote","message":{"content":"sdk prompt"}}',
                '{"type":"assistant","timestamp":"2026-03-27T10:00:00Z","message":{"content":"sdk reply"}}',
            ]
        ),
        encoding="utf-8",
    )

    provider = ClaudeNativeSessionProvider(root=str(projects_root), history_path=str(history_path))

    items = provider.list_metadata(working_path)

    assert {item.native_session_id for item in items} == {"sess_history", "sess_sdk"}


def test_claude_provider_keeps_legacy_slash_only_project_dir(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    history_path = tmp_path / "history.jsonl"
    projects_root.mkdir(parents=True, exist_ok=True)
    history_path.write_text("", encoding="utf-8")

    working_path = "/Users/cyh/my.repo"
    legacy_dir = projects_root / working_path.replace("/", "-")
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "sess_legacy.jsonl").write_text(
        "\n".join(
            [
                '{"type":"user","timestamp":"2026-03-27T09:59:00Z","cwd":"/Users/cyh/my.repo","message":{"content":"legacy prompt"}}',
                '{"type":"assistant","timestamp":"2026-03-27T10:00:00Z","message":{"content":"legacy reply"}}',
            ]
        ),
        encoding="utf-8",
    )

    provider = ClaudeNativeSessionProvider(root=str(projects_root), history_path=str(history_path))

    items = provider.list_metadata(working_path)

    assert [item.native_session_id for item in items] == ["sess_legacy"]


def test_claude_provider_does_not_scan_unrelated_project_jsonl(tmp_path: Path, monkeypatch) -> None:
    projects_root = tmp_path / "projects"
    history_path = tmp_path / "history.jsonl"
    projects_root.mkdir(parents=True, exist_ok=True)
    history_path.write_text("", encoding="utf-8")

    working_path = "/Users/cyh/vibe-remote"
    candidate_dir = projects_root / encode_project_path(working_path)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    target_jsonl = candidate_dir / "sess_target.jsonl"
    target_jsonl.write_text(
        '{"type":"assistant","timestamp":"2026-03-27T10:00:00Z","message":{"content":"done"}}\n',
        encoding="utf-8",
    )

    unrelated_dir = projects_root / "-Users-cyh-other"
    unrelated_dir.mkdir(parents=True, exist_ok=True)
    unrelated_jsonl = unrelated_dir / "sess_other.jsonl"
    unrelated_jsonl.write_text(
        '{"type":"assistant","timestamp":"2026-03-27T10:00:00Z","message":{"content":"should not read"}}\n',
        encoding="utf-8",
    )

    read_paths: list[Path] = []
    original_read_json_lines = claude_module.read_json_lines

    def _tracking_read_json_lines(path: Path) -> list[dict]:
        read_paths.append(Path(path))
        return original_read_json_lines(path)

    monkeypatch.setattr(claude_module, "read_json_lines", _tracking_read_json_lines)
    provider = ClaudeNativeSessionProvider(root=str(projects_root), history_path=str(history_path))

    items = provider.list_metadata(working_path)

    assert [item.native_session_id for item in items] == ["sess_target"]
    assert target_jsonl in read_paths
    assert unrelated_jsonl not in read_paths


def test_claude_provider_uses_global_index_fallback_without_scanning_all_jsonl(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    history_path = tmp_path / "history.jsonl"
    projects_root.mkdir(parents=True, exist_ok=True)
    history_path.write_text("", encoding="utf-8")

    working_path = "/Users/cyh/vibe-remote"
    indexed_dir = projects_root / "-Users-cyh"
    indexed_dir.mkdir(parents=True, exist_ok=True)
    session_jsonl = indexed_dir / "sess_idx.jsonl"
    session_jsonl.write_text(
        "\n".join(
            [
                '{"type":"user","timestamp":"2026-03-27T09:59:00Z","cwd":"/Users/cyh/vibe-remote","message":{"content":"hello"}}',
                '{"type":"assistant","timestamp":"2026-03-27T10:00:00Z","message":{"content":"reply from indexed session"}}',
            ]
        ),
        encoding="utf-8",
    )
    (indexed_dir / "sessions-index.json").write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "sessionId": "sess_idx",
                        "projectPath": "/Users/cyh/vibe-remote",
                        "created": "2026-03-27T09:59:00Z",
                        "modified": "2026-03-27T10:00:00Z",
                        "firstPrompt": "hello",
                        "fullPath": str(session_jsonl),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    provider = ClaudeNativeSessionProvider(root=str(projects_root), history_path=str(history_path))

    items = provider.list_metadata(working_path)

    assert [item.native_session_id for item in items] == ["sess_idx"]
    hydrated = provider.hydrate_preview(items[0])
    assert hydrated.last_agent_message == "reply from indexed session"
    assert hydrated.last_agent_tail.startswith("...")
    assert "indexed session" in hydrated.last_agent_tail


def test_codex_provider_skips_empty_rollout_path(monkeypatch) -> None:
    provider = CodexNativeSessionProvider(db_path="/tmp/does-not-matter.sqlite")
    item = NativeResumeSession(
        agent="codex",
        agent_prefix="cx",
        native_session_id="thread_1",
        working_path="/tmp/project",
        created_at=None,
        updated_at=None,
        sort_ts=1.0,
        locator={"title": "Fallback title", "rollout_path": ""},
    )

    called = False

    def _unexpected_read_json_lines(_path: Path) -> list[dict]:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(codex_module, "read_json_lines", _unexpected_read_json_lines)

    hydrated = provider.hydrate_preview(item)

    assert called is False
    assert hydrated.last_agent_message == "Fallback title"
    assert hydrated.last_agent_tail == "Fallback title"


def test_native_session_service_preserves_agent_visibility_when_limited() -> None:
    def _item(agent: str, prefix: str, session_id: str, sort_ts: float) -> NativeResumeSession:
        return NativeResumeSession(
            agent=agent,
            agent_prefix=prefix,
            native_session_id=session_id,
            working_path="/tmp/project",
            created_at=None,
            updated_at=None,
            sort_ts=sort_ts,
            last_agent_message=session_id,
            last_agent_tail=f"...{session_id[-4:]}",
        )

    oc_provider = SimpleNamespace(
        agent_name="opencode",
        list_metadata=lambda working_path: [_item("opencode", "oc", f"oc_{i}", 200 - i) for i in range(5)],
        hydrate_preview=lambda item: item,
    )
    cc_provider = SimpleNamespace(
        agent_name="claude",
        list_metadata=lambda working_path: [_item("claude", "cc", "cc_1", 50)],
        hydrate_preview=lambda item: item,
    )
    cx_provider = SimpleNamespace(
        agent_name="codex",
        list_metadata=lambda working_path: [_item("codex", "cx", f"cx_{i}", 100 - i) for i in range(5)],
        hydrate_preview=lambda item: item,
    )

    service = AgentNativeSessionService(providers=[oc_provider, cc_provider, cx_provider])

    items = service.list_recent_sessions("/tmp/project", limit=5)

    assert len(items) == 5
    assert {item.agent for item in items} == {"opencode", "claude", "codex"}


def test_native_session_service_loads_default_providers_lazily(monkeypatch) -> None:
    calls: list[str] = []

    class _StubProvider:
        agent_name = "claude"

        def list_metadata(self, working_path: str) -> list[NativeResumeSession]:
            return []

        def hydrate_preview(self, item: NativeResumeSession) -> NativeResumeSession:
            return item

    def _fake_import_module(module_path: str):
        calls.append(module_path)
        return SimpleNamespace(ClaudeNativeSessionProvider=_StubProvider)

    monkeypatch.setattr(service_module.importlib, "import_module", _fake_import_module)
    service = AgentNativeSessionService(
        provider_specs=(
            service_module.NativeSessionProviderSpec(
                agent_name="claude",
                module_path="modules.agents.native_sessions.claude",
                class_name="ClaudeNativeSessionProvider",
            ),
        )
    )

    assert calls == []

    assert service.list_recent_sessions("/tmp/project", limit=5) == []
    assert calls == ["modules.agents.native_sessions.claude"]


def test_native_session_lightweight_imports_do_not_require_sqlite() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    script = """
import importlib.abc

class BlockSqlite(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "sqlite3" or fullname.startswith("sqlite3.") or fullname == "_sqlite3":
            raise ImportError("blocked sqlite for test")
        return None

import sys
sys.meta_path.insert(0, BlockSqlite())

for module_name in [
    "modules.agents.native_sessions",
    "core.handlers.command_handlers",
    "core.handlers.session_handler",
]:
    __import__(module_name)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_build_tail_preview_strips_edge_symbols() -> None:
    assert build_tail_preview("前文很多很多很多。**最后一句话？？？**") == "...很多。**最后一句话"


def test_build_resume_preview_preserves_line_breaks() -> None:
    text = "第一段第一行\n第二行\n\n第三行\n---\n[button]"

    assert build_resume_preview(text, limit=200) == "第一段第一行\n第二行\n\n第三行"
