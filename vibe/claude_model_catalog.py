from __future__ import annotations

import json
import mmap
import re
from pathlib import Path
from typing import Iterable

DEFAULT_CLAUDE_MODEL_ALIASES: tuple[str, ...] = ("opus", "sonnet", "haiku")
FALLBACK_CLAUDE_MODELS: tuple[str, ...] = (
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-opus-4",
    "claude-sonnet-4",
    "claude-haiku-4",
)

_CLAUDE_FAMILY_ORDER = {
    "opus": 0,
    "sonnet": 1,
    "haiku": 2,
}
_CLAUDE_MODEL_PATTERN = re.compile(rb"claude-(?:opus|sonnet|haiku)-\d+(?:-\d+)*(?:-\d{8})?")


def get_catalog_path(repo_root: Path | None = None) -> Path:
    base_dir = repo_root if repo_root is not None else Path(__file__).resolve().parent
    return base_dir / "data" / "claude_models.json"


def load_catalog_models(path: Path | None = None) -> list[str]:
    catalog_path = path or get_catalog_path()
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return list(FALLBACK_CLAUDE_MODELS)
    except Exception:
        return list(FALLBACK_CLAUDE_MODELS)

    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return list(FALLBACK_CLAUDE_MODELS)

    normalized = _dedupe_str_values(models)
    return normalized or list(FALLBACK_CLAUDE_MODELS)


def infer_bundle_path_from_cli(cli_path: str | None) -> Path | None:
    if not cli_path:
        return None

    resolved = Path(cli_path).expanduser().resolve()
    candidates = (
        resolved,
        resolved.parent / "cli.js",
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def infer_models_from_bundle(bundle_path: Path) -> list[str]:
    matches: set[str] = set()
    with bundle_path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
        for match in _CLAUDE_MODEL_PATTERN.finditer(mapped):
            matches.add(match.group(0).decode("utf-8"))
    return sort_catalog_models(matches)


def sort_catalog_models(models: Iterable[str]) -> list[str]:
    normalized = _dedupe_str_values(models)

    def sort_key(model: str) -> tuple[int, tuple[int, ...], str]:
        parts = model.split("-")
        family = parts[1] if len(parts) > 1 else ""
        version_numbers = tuple(-int(part) for part in parts[2:] if part.isdigit())
        return (
            _CLAUDE_FAMILY_ORDER.get(family, len(_CLAUDE_FAMILY_ORDER)),
            version_numbers,
            model,
        )

    return sorted(normalized, key=sort_key)


def write_catalog_models(models: Iterable[str], path: Path | None = None) -> Path:
    catalog_path = path or get_catalog_path()
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "models": sort_catalog_models(models),
    }
    catalog_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return catalog_path


def _dedupe_str_values(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized
