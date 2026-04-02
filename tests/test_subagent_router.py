from pathlib import Path

import modules.agents.subagent_router as subagent_router
from modules.agents.subagent_router import (
    list_codex_subagents,
    load_claude_subagent,
    load_codex_subagent,
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


def test_list_codex_subagents_prefers_project_override(tmp_path: Path):
    global_agent_dir = tmp_path / ".codex" / "agents"
    global_agent_dir.mkdir(parents=True)
    (global_agent_dir / "reviewer.toml").write_text(
        '\n'.join(
            [
                'name = "reviewer"',
                'description = "Global reviewer"',
                'developer_instructions = "Review globally."',
                'model = "gpt-5.4-mini"',
                'model_reasoning_effort = "medium"',
            ]
        ),
        encoding="utf-8",
    )

    project_root = tmp_path / "repo"
    project_agent_dir = project_root / ".codex" / "agents"
    project_agent_dir.mkdir(parents=True)
    (project_agent_dir / "reviewer.toml").write_text(
        '\n'.join(
            [
                'name = "reviewer"',
                'description = "Project reviewer"',
                'developer_instructions = "Review the repo-specific changes."',
                'model = "gpt-5.4"',
                'model_reasoning_effort = "high"',
            ]
        ),
        encoding="utf-8",
    )

    definitions = list_codex_subagents(search_root=tmp_path / ".codex", project_root=project_root)

    reviewer = definitions["reviewer"]
    assert reviewer.name == "reviewer"
    assert reviewer.description == "Project reviewer"
    assert reviewer.developer_instructions == "Review the repo-specific changes."
    assert reviewer.model == "gpt-5.4"
    assert reviewer.reasoning_effort == "high"
    assert reviewer.source == "project"
    assert reviewer.path == project_agent_dir / "reviewer.toml"


def test_load_codex_subagent_reads_global_agent_definition(tmp_path: Path):
    global_agent_dir = tmp_path / ".codex" / "agents"
    global_agent_dir.mkdir(parents=True)
    agent_file = global_agent_dir / "reviewer.toml"
    agent_file.write_text(
        '\n'.join(
            [
                'name = "reviewer"',
                'description = "Checks risks"',
                'developer_instructions = "Focus on regressions."',
                'model = "gpt-5.4-mini"',
                'model_reasoning_effort = "low"',
            ]
        ),
        encoding="utf-8",
    )

    definition = load_codex_subagent("REVIEWER", search_root=tmp_path / ".codex")

    assert definition
    assert definition.name == "reviewer"
    assert definition.description == "Checks risks"
    assert definition.developer_instructions == "Focus on regressions."
    assert definition.model == "gpt-5.4-mini"
    assert definition.reasoning_effort == "low"
    assert definition.source == "global"
    assert definition.path == agent_file


def test_parse_codex_agent_definition_uses_fallback_toml_parser(tmp_path: Path, monkeypatch):
    agent_file = tmp_path / "reviewer.toml"
    agent_file.write_text(
        '\n'.join(
            [
                'name = "reviewer"',
                'description = "Checks risks"',
                'developer_instructions = "Focus on regressions."',
            ]
        ),
        encoding="utf-8",
    )

    class _FallbackParser:
        @staticmethod
        def loads(text: str):
            assert 'name = "reviewer"' in text
            return {
                "name": "reviewer",
                "description": "Checks risks",
                "developer_instructions": "Focus on regressions.",
            }

    monkeypatch.setattr(subagent_router, "_toml_parser", _FallbackParser)

    definition = subagent_router._parse_codex_agent_definition(agent_file, source="global")

    assert definition
    assert definition.name == "reviewer"
    assert definition.description == "Checks risks"
    assert definition.developer_instructions == "Focus on regressions."
