from pathlib import Path
from types import SimpleNamespace

from modules.agents.native_sessions.base import build_resume_preview, build_tail_preview
from modules.agents.native_sessions.claude import ClaudeNativeSessionProvider
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


def test_build_tail_preview_strips_edge_symbols() -> None:
    assert build_tail_preview("前文很多很多很多。**最后一句话？？？**") == "...很多。**最后一句话"


def test_build_resume_preview_preserves_line_breaks() -> None:
    text = "第一段第一行\n第二行\n\n第三行\n---\n[button]"

    assert build_resume_preview(text, limit=200) == "第一段第一行\n第二行\n\n第三行"
