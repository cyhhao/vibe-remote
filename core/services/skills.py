"""Business API for Agent Skills — a thin shell over the ``askill`` CLI.

Wraps ``askill <cmd> --json`` (github.com/avibe-bot/askill) so the Web UI can
manage global + project skills across backends without owning install logic.
The CLI is the source of truth; this layer maps Vibe Remote concepts onto
askill's flags, runs the binary, and parses the documented ``--json``
contract into plain dicts.

Layering (per ``docs/plans/workbench-dispatch-architecture.md`` §6, and the
build plan in ``docs/plans/workbench-skills-page.md``):

* Transport-agnostic and dependency-injected: the resolved ``askill`` binary
  path is passed in by the caller (``vibe.api`` resolves it via
  ``resolve_cli_path("askill")``), so ``core/`` never imports ``vibe/`` and
  the service owns no process-level config.
* Functions return plain ``dict`` payloads (the askill envelope, optionally
  enriched). Failures raise ``LookupError("askill_not_found")`` or
  ``SkillsError(code, message)`` for the route layer to translate to HTTP.

askill ``--json`` is only implemented for ``add``/``find``/``list``/``remove``
(askill#11 tracks ``--json`` for ``check``/``info`` plus richer ``list``
metadata). Until that lands, installed-skill ``description``/``version`` are
enriched here from the canonical ``SKILL.md`` frontmatter (see
``_enrich_installed``); delete that path once the CLI carries the fields.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0

# Vibe Remote backend id <-> askill agent id. askill targets "agents"; our
# product calls the same things "backends". One map, used everywhere.
BACKEND_TO_AGENT: dict[str, str] = {
    "claude": "claude-code",
    "opencode": "opencode",
    "codex": "codex",
}
AGENT_TO_BACKEND: dict[str, str] = {agent: backend for backend, agent in BACKEND_TO_AGENT.items()}

try:  # PyYAML is a transitive dep (scenario catalogs use it); degrade gracefully if absent.
    import yaml as _yaml
except Exception:  # noqa: BLE001 - optional dependency
    _yaml = None


class SkillsError(Exception):
    """A failure with a stable ``code`` the route layer maps to HTTP/i18n."""

    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


async def _run_askill(
    askill_path: str,
    args: list[str],
    *,
    cwd: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run ``askill <args> --json`` and parse stdout as JSON.

    In ``--json`` mode askill emits a machine-readable envelope even on a
    non-zero exit, so we parse stdout regardless of the return code and let
    callers branch on ``data["ok"]`` / ``data["error"]``. Spawn, timeout, and
    parse failures raise (``LookupError`` for a missing binary, ``SkillsError``
    otherwise).

    Subprocess handling mirrors ``core/agent_auth_service`` (the established
    ``create_subprocess_exec`` + ``wait_for(communicate())`` pattern).
    """
    if not askill_path:
        raise LookupError("askill_not_found")
    cmd = [askill_path, *args, "--json"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as err:
        raise LookupError("askill_not_found") from err
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        await proc.communicate()
        logger.info("askill timed out after %ss: %s", timeout, " ".join(args))
        raise SkillsError("askill_timeout", f"askill timed out after {timeout:.0f}s")

    text = (out or b"").decode("utf-8", errors="replace").strip()
    if not text:
        detail = (err or b"").decode("utf-8", errors="replace").strip()
        logger.info("askill produced no stdout (%s): %s", " ".join(args), detail[:300])
        raise SkillsError("askill_no_output", detail or "askill produced no output")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.info("askill emitted non-JSON output (%s): %s", " ".join(args), text[:300])
        raise SkillsError("askill_bad_json", "could not parse askill output") from exc
    if not isinstance(data, dict):
        raise SkillsError("askill_bad_json", "askill output was not a JSON object")
    return data


def _agent_flags(backends: Optional[list[str]]) -> list[str]:
    """Expand selected Vibe backends into repeated ``-a <agent>`` flags."""
    flags: list[str] = []
    for backend in backends or []:
        agent = BACKEND_TO_AGENT.get(backend)
        if agent:
            flags += ["-a", agent]
        else:
            raise SkillsError("invalid_backend", f"unknown backend: {backend}")
    return flags


def _scope_flag(scope: str) -> list[str]:
    if scope == "global":
        return ["-g"]
    if scope == "project":
        return ["-p"]
    if scope == "all":
        return []
    raise SkillsError("invalid_scope", f"unknown scope: {scope}")


# --- public API -----------------------------------------------------------


async def list_skills(
    askill_path: str,
    *,
    scope: str = "all",
    project_dir: Optional[str] = None,
    backends: Optional[list[str]] = None,
) -> dict[str, Any]:
    """List installed skills. ``scope`` is ``all`` | ``global`` | ``project``.

    Project-scoped lists run with ``cwd=project_dir`` so askill resolves the
    repo's ``.agents/skills``. Each installed entry is enriched with
    ``description``/``version`` from its ``SKILL.md`` (see module docstring).
    """
    args = ["list", *_scope_flag(scope), *_agent_flags(backends)]
    cwd = project_dir if scope != "global" else None
    data = await _run_askill(askill_path, args, cwd=cwd)
    if data.get("ok"):
        for skill in data.get("skills", []):
            _enrich_installed(skill)
    return data


async def preview_source(
    askill_path: str,
    source: str,
    *,
    project_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Discover the skills a source contains without installing.

    Maps to ``askill add <source> --list --json``. ``source`` is a slug
    (``gh:owner/repo[@name]``, ``gh:owner/repo/path``), a GitHub URL, or a
    local directory (e.g. an unpacked ``.zip``). Returns
    ``{action:"preview", source, skills:[{name, description, ...}]}``.
    """
    if not source:
        raise SkillsError("missing_source", "no source provided")
    args = ["add", source, "--list"]
    return await _run_askill(askill_path, args, cwd=project_dir)


async def add_skill(
    askill_path: str,
    source: str,
    *,
    scope: str = "project",
    project_dir: Optional[str] = None,
    backends: Optional[list[str]] = None,
    all_skills: bool = False,
    copy: bool = False,
) -> dict[str, Any]:
    """Install skill(s) from a source. Non-interactive (``-y``).

    Maps to ``askill add <source> [-g] [-a <agent>...] [--all] [--copy] -y``.
    ``scope`` must be ``global`` or ``project`` for installs.
    """
    if not source:
        raise SkillsError("missing_source", "no source provided")
    if scope not in ("global", "project"):
        raise SkillsError("invalid_scope", "install scope must be global or project")
    args = ["add", source, *_scope_flag(scope), *_agent_flags(backends)]
    if all_skills:
        args.append("--all")
    if copy:
        args.append("--copy")
    args.append("-y")
    cwd = project_dir if scope == "project" else None
    return await _run_askill(askill_path, args, cwd=cwd)


async def remove_skill(
    askill_path: str,
    name: str,
    *,
    scope: str = "project",
    project_dir: Optional[str] = None,
    backends: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Remove an installed skill, optionally from specific backends only.

    Maps to ``askill remove <name> [-g] [-a <agent>...]``.
    """
    if not name:
        raise SkillsError("missing_skill", "no skill name provided")
    if scope not in ("global", "project"):
        raise SkillsError("invalid_scope", "remove scope must be global or project")
    args = ["remove", name, *_scope_flag(scope), *_agent_flags(backends)]
    cwd = project_dir if scope == "project" else None
    return await _run_askill(askill_path, args, cwd=cwd)


async def find_skills(askill_path: str, query: str = "") -> dict[str, Any]:
    """Search the askill.sh registry. Maps to ``askill find <query>``.

    Returns ``{ok, query, count, skills:[{name, description, owner, repo,
    tags, stars, aiScore, aiBreakdown, ...}]}``.
    """
    args = ["find"]
    if query:
        args.append(query)
    return await _run_askill(askill_path, args)


# --- enrichment (temporary, until askill#11 carries list metadata) ---------


def _enrich_installed(skill: dict[str, Any]) -> None:
    """Fill ``description``/``version`` from the skill's ``SKILL.md``.

    askill ``list --json`` currently returns only ``{name, scope, path,
    agents}``; the UI wants a description + version per row. The canonical
    install path is in the payload, so read its frontmatter. Remove once
    askill#11 ships these fields natively. Best-effort: never raises.
    """
    path = skill.get("path")
    if not path or skill.get("description"):
        return
    frontmatter = _read_frontmatter(Path(path) / "SKILL.md")
    if not frontmatter:
        return
    if frontmatter.get("description"):
        skill.setdefault("description", str(frontmatter["description"]))
    if frontmatter.get("version"):
        skill.setdefault("version", str(frontmatter["version"]))


def _read_frontmatter(md_path: Path) -> dict[str, Any]:
    """Parse the leading ``---`` YAML frontmatter block of a SKILL.md."""
    try:
        text = md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip("\n")
    if _yaml is not None:
        try:
            parsed = _yaml.safe_load(block)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:  # noqa: BLE001 - tolerate malformed frontmatter
            return {}
    # Minimal fallback: top-level "key: value" scalars only.
    out: dict[str, Any] = {}
    for line in block.splitlines():
        if ":" in line and not line[:1].isspace() and not line.startswith("#"):
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip().strip("'\"")
    return out
