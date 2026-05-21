"""Vibe-owned Agent catalog and import helpers."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from uuid import uuid4

import yaml
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from config import paths
from storage.db import SqliteInvalidationProbe, create_sqlite_engine
from storage.importer import ensure_sqlite_state, resolve_primary_platform_from_config
from storage.migrations import run_migrations
from storage.models import agents

DEFAULT_AGENT_NAME = "default"
SUPPORTED_AGENT_BACKENDS = {"codex", "claude", "opencode"}
_UNSET = object()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_agent_name(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(name or "").strip().lower()).strip("-_")
    if not normalized:
        raise ValueError("agent name is required")
    return normalized


def validate_agent_backend(backend: str) -> str:
    value = str(backend or "").strip().lower()
    if value not in SUPPORTED_AGENT_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_AGENT_BACKENDS))
        raise ValueError(f"unsupported agent backend: {backend}. Supported backends: {supported}")
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class VibeAgent:
    id: str
    name: str
    normalized_name: str
    backend: str
    description: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    system_prompt: Optional[str] = None
    source: str = "user"
    source_ref: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentImportCandidate:
    name: str
    backend: str
    description: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    system_prompt: Optional[str] = None
    source: str = "import"
    source_ref: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentImportResult:
    imported: list[VibeAgent]
    skipped: list[dict[str, Any]]


class VibeAgentStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or paths.get_sqlite_state_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if db_path is None:
            ensure_sqlite_state(primary_platform=resolve_primary_platform_from_config(paths.get_state_dir()))
        else:
            run_migrations(self.db_path)
        self.engine = create_sqlite_engine(self.db_path)
        self._probe = SqliteInvalidationProbe(self.engine)

    def close(self) -> None:
        self._probe.close()
        self.engine.dispose()

    def maybe_reload(self) -> bool:
        return self._probe.has_external_write()

    def list_agents(self) -> list[VibeAgent]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(agents).order_by(agents.c.name)).mappings()
            return [self._from_row(row) for row in rows]

    def get(self, name: str) -> Optional[VibeAgent]:
        normalized = normalize_agent_name(name)
        with self.engine.connect() as conn:
            row = conn.execute(
                select(agents).where(agents.c.normalized_name == normalized).limit(1)
            ).mappings().first()
            return self._from_row(row) if row else None

    def require(self, name: str) -> VibeAgent:
        agent = self.get(name)
        if agent is None:
            raise ValueError(f"agent '{name}' not found")
        return agent

    def create(
        self,
        *,
        name: str,
        backend: str,
        description: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        system_prompt: Optional[str] = None,
        source: str = "user",
        source_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> VibeAgent:
        normalized = normalize_agent_name(name)
        now = _utc_now_iso()
        agent = VibeAgent(
            id=uuid4().hex[:12],
            name=str(name).strip(),
            normalized_name=normalized,
            backend=validate_agent_backend(backend),
            description=_clean_optional(description),
            model=_clean_optional(model),
            reasoning_effort=_clean_optional(reasoning_effort),
            system_prompt=_clean_optional(system_prompt),
            source=str(source or "user"),
            source_ref=_clean_optional(source_ref),
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(agents.insert().values(**self._values(agent)))
        except IntegrityError as exc:
            raise ValueError(f"agent '{name}' already exists") from exc
        return agent

    def update(
        self,
        name: str,
        *,
        description: Any = _UNSET,
        model: Any = _UNSET,
        reasoning_effort: Any = _UNSET,
        system_prompt: Any = _UNSET,
        metadata: Any = _UNSET,
    ) -> VibeAgent:
        existing = self.require(name)
        values: dict[str, Any] = {"updated_at": _utc_now_iso()}
        if description is not _UNSET:
            values["description"] = _clean_optional(description)
        if model is not _UNSET:
            values["model"] = _clean_optional(model)
        if reasoning_effort is not _UNSET:
            values["reasoning_effort"] = _clean_optional(reasoning_effort)
        if system_prompt is not _UNSET:
            values["system_prompt"] = _clean_optional(system_prompt)
        if metadata is not _UNSET:
            values["metadata_json"] = _json_dumps(dict(metadata or {}))
        with self.engine.begin() as conn:
            conn.execute(agents.update().where(agents.c.id == existing.id).values(**values))
        return self.require(name)

    def remove(self, name: str) -> bool:
        normalized = normalize_agent_name(name)
        with self.engine.begin() as conn:
            result = conn.execute(agents.delete().where(agents.c.normalized_name == normalized))
            return bool(result.rowcount)

    def import_candidates(self, candidates: Iterable[AgentImportCandidate]) -> AgentImportResult:
        imported: list[VibeAgent] = []
        skipped: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                if self.get(candidate.name):
                    skipped.append({"name": candidate.name, "reason": "name_conflict"})
                    continue
                imported.append(
                    self.create(
                        name=candidate.name,
                        backend=candidate.backend,
                        description=candidate.description,
                        model=candidate.model,
                        reasoning_effort=candidate.reasoning_effort,
                        system_prompt=candidate.system_prompt,
                        source=candidate.source,
                        source_ref=candidate.source_ref,
                        metadata=candidate.metadata,
                    )
                )
            except Exception as exc:
                skipped.append({"name": candidate.name, "reason": "invalid", "error": str(exc)})
        return AgentImportResult(imported=imported, skipped=skipped)

    def ensure_default_agent(self, *, backend: str = "claude") -> VibeAgent:
        existing = self.get(DEFAULT_AGENT_NAME)
        if existing:
            return existing
        return self.create(
            name=DEFAULT_AGENT_NAME,
            backend=backend,
            description="Default Vibe Remote agent.",
            source="builtin",
            metadata={"builtin": True},
        )

    @staticmethod
    def _from_row(row: Any) -> VibeAgent:
        return VibeAgent(
            id=row["id"],
            name=row["name"],
            normalized_name=row["normalized_name"],
            backend=row["backend"],
            description=row["description"],
            model=row["model"],
            reasoning_effort=row["reasoning_effort"],
            system_prompt=row["system_prompt"],
            source=row["source"],
            source_ref=row["source_ref"],
            metadata=_json_loads(row["metadata_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _values(agent: VibeAgent) -> dict[str, Any]:
        return {
            "id": agent.id,
            "name": agent.name,
            "normalized_name": agent.normalized_name,
            "description": agent.description,
            "backend": agent.backend,
            "model": agent.model,
            "reasoning_effort": agent.reasoning_effort,
            "system_prompt": agent.system_prompt,
            "source": agent.source,
            "source_ref": agent.source_ref,
            "metadata_json": _json_dumps(agent.metadata),
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
        }


def parse_agent_file(path: Path, *, backend: str) -> AgentImportCandidate:
    backend = validate_agent_backend(backend)
    raw = path.read_text(encoding="utf-8")
    header: dict[str, Any] = {}
    body = raw.strip()
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            header = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()
    name = str(header.get("name") or path.stem).strip()
    description = header.get("description")
    model = header.get("model")
    reasoning_effort = header.get("reasoning_effort") or header.get("reasoningEffort")
    metadata = {
        key: value
        for key, value in header.items()
        if key not in {"name", "description", "model", "reasoning_effort", "reasoningEffort"}
    }
    return AgentImportCandidate(
        name=name,
        backend=backend,
        description=str(description).strip() if description else None,
        model=str(model).strip() if model else None,
        reasoning_effort=str(reasoning_effort).strip() if reasoning_effort else None,
        system_prompt=body or None,
        source="file",
        source_ref=str(path),
        metadata=metadata,
    )


def iter_global_agent_files(source: str) -> list[tuple[Path, str]]:
    source_key = str(source or "").strip().lower()
    home = Path.home()
    if source_key == "claude":
        return [(path, "claude") for path in sorted((home / ".claude" / "agents").glob("*.md"))]
    if source_key == "codex":
        search_dirs = [home / ".codex" / "agents"]
        return [(path, "codex") for directory in search_dirs for path in sorted(directory.glob("*.md"))]
    if source_key == "opencode":
        search_dirs = [
            home / ".config" / "opencode" / "agent",
            home / ".config" / "opencode" / "agents",
        ]
        return [(path, "opencode") for directory in search_dirs for path in sorted(directory.glob("*.md"))]
    raise ValueError("--from must be one of: claude, codex, opencode")


def _clean_optional(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
