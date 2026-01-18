from pathlib import Path

from modules.agents.subagent_router import (
    load_claude_subagent,
    normalize_subagent_name,
    parse_subagent_prefix,
)


def test_parse_subagent_prefix_basic():
    match = parse_subagent_prefix("Plan: do something")
    assert match
    assert match.name == "Plan"
    assert match.message == "do something"


def test_parse_subagent_prefix_chinese_colon_and_whitespace():
    match = parse_subagent_prefix("  \nPlan：   test")
    assert match
    assert match.name == "Plan"
    assert match.message == "test"


def test_parse_subagent_prefix_empty_body_returns_none():
    assert parse_subagent_prefix("Plan:") is None
    assert parse_subagent_prefix("Plan：   ") is None


def test_normalize_subagent_name():
    assert normalize_subagent_name(" Plan ") == "plan"
    assert normalize_subagent_name("PLAN") == "plan"


def test_load_claude_subagent_from_agent_dir(tmp_path: Path):
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    agent_file = agent_dir / "code-reviewer.md"
    agent_file.write_text(
        "---\nname: code-reviewer\nmodel: sonnet\n---\n\nBody",
        encoding="utf-8",
    )

    definition = load_claude_subagent("CODE-REVIEWER", search_root=tmp_path)
    assert definition
    assert definition.name == "code-reviewer"
    assert definition.model == "sonnet"
