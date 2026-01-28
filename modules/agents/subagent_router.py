from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

import yaml

logger = logging.getLogger(__name__)


_PREFIX_PATTERN = re.compile(r"^([^\s:：]+)\s*[:：]\s*(.*)$", re.DOTALL)


def _yaml_safe_load(text: str) -> dict:
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class SubagentDefinition:
    name: str
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None


@dataclass(frozen=True)
class PrefixMatch:
    name: str
    message: str


def parse_subagent_prefix(message: str) -> Optional[PrefixMatch]:
    if not message:
        return None

    trimmed = message.lstrip()
    match = _PREFIX_PATTERN.match(trimmed)
    if not match:
        return None

    name = match.group(1).strip()
    body = match.group(2)
    if not name:
        return None

    if not body or not body.strip():
        return None

    return PrefixMatch(name=name, message=body.strip())


def normalize_subagent_name(name: str) -> str:
    return (name or "").strip().lower()


def list_claude_subagents(
    search_root: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> Dict[str, SubagentDefinition]:
    """List all available Claude subagents.
    
    Args:
        search_root: Global search root (default: ~/.claude)
        project_root: Project-specific root to also search (e.g., <cwd>)
    
    Returns:
        Dict mapping normalized agent names to their definitions.
        Project agents override global agents with same name.
    """
    definitions: Dict[str, SubagentDefinition] = {}
    
    # Search global agents first
    root = search_root or (Path.home() / ".claude")
    if root.exists():
        agent_files: list[Path] = []
        candidate_dirs = _find_claude_agent_dirs(root)
        for directory in candidate_dirs:
            agent_files.extend(directory.glob("*.md"))

        for agent_file in agent_files:
            definition = _parse_claude_agent_definition(agent_file)
            if not definition:
                continue
            key = normalize_subagent_name(definition.name)
            if key:
                definitions[key] = definition
    
    # Search project agents (override global)
    if project_root:
        project_agents_dir = project_root / ".claude" / "agents"
        if project_agents_dir.exists() and project_agents_dir.is_dir():
            for agent_file in project_agents_dir.glob("*.md"):
                definition = _parse_claude_agent_definition(agent_file)
                if not definition:
                    continue
                key = normalize_subagent_name(definition.name)
                if key:
                    definitions[key] = definition
    
    return definitions


def load_claude_subagent(
    name: str,
    search_root: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> Optional[SubagentDefinition]:
    """Load a specific Claude subagent by name.
    
    Args:
        name: Agent name to search for
        search_root: Global search root (default: ~/.claude)
        project_root: Project-specific root to also search (e.g., <cwd>)
    
    Returns:
        SubagentDefinition if found, None otherwise.
        Project agents take precedence over global agents.
    """
    normalized = normalize_subagent_name(name)
    if not normalized:
        return None
    return list_claude_subagents(search_root, project_root).get(normalized)


def _find_claude_agent_dirs(root: Path) -> Iterable[Path]:
    agent_dirs = []
    if (root / "agents").exists():
        agent_dirs.append(root / "agents")

    for path in root.rglob("agents"):
        if path.is_dir():
            agent_dirs.append(path)

    seen = set()
    unique = []
    for path in agent_dirs:
        resolved = os.path.normpath(str(path))
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _parse_claude_agent_definition(path: Path) -> Optional[SubagentDefinition]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.debug("Failed to read subagent definition file %s: %s", path, e)
        return None

    if not text.startswith("---"):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    header = parts[1]
    try:
        data = _yaml_safe_load(header)
    except Exception as e:
        logger.debug("Failed to parse YAML header in subagent definition %s: %s", path, e)
        return None

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    model = data.get("model")
    if not isinstance(model, str) or not model.strip():
        model = None

    reasoning_effort = data.get("reasoning_effort") or data.get("reasoningEffort")
    if not isinstance(reasoning_effort, str) or not reasoning_effort.strip():
        reasoning_effort = None

    return SubagentDefinition(name=name.strip(), model=model, reasoning_effort=reasoning_effort)
