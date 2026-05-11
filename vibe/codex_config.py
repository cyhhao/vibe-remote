"""Helpers for writing Codex's on-disk auth configuration.

The Codex CLI / ``codex app-server`` reads two files at launch time:

- ``~/.codex/config.toml`` — model + provider preferences (including the
  ``model_provider`` selector and the ``[model_providers.<id>]`` table that
  carries ``base_url``).
- ``~/.codex/auth.json`` — credential bag; the ``OPENAI_API_KEY`` field is
  the one Codex consumes for API-key mode.

This module mediates writes to those files so the Settings → Backends →
Codex UI can flip between OAuth (ChatGPT login) and API-key modes without
the user dropping into a terminal. The persistent app-server picks up
changes via ``restart_backend('codex')``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# A TOML "bare key" is the unquoted form — anything outside this character
# set must be emitted as a quoted key. Codex specifically uses quoted keys
# under ``[projects."/absolute/path"]`` to scope per-directory settings,
# so the emitter has to round-trip those correctly.
_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Provider id we manage in ``[model_providers.<id>]``. Codex ships with a
# built-in ``openai`` provider; if the user has hand-edited that block we
# leave their fields alone except for ``base_url`` when one is supplied.
MANAGED_PROVIDER_ID = "openai"


def get_codex_home(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".codex"


def get_codex_config_paths(home: Path | None = None) -> tuple[Path, Path]:
    """Return ``(config.toml, auth.json)`` paths under ``~/.codex``."""
    codex_home = get_codex_home(home)
    return codex_home / "config.toml", codex_home / "auth.json"


def _load_toml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        try:
            import tomllib  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - py<3.11 fallback
            import tomli as tomllib  # type: ignore[no-redef]
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Codex config.toml parse failed (%s); rewriting from empty", exc)
        return {}


def _load_auth(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("Codex auth.json parse failed (%s); rewriting from empty", exc)
    return {}


def _format_toml_key(key: str) -> str:
    """Quote a TOML key when it falls outside the bare-key character class.

    Plain identifier keys like ``model_provider`` round-trip as-is; keys
    that contain dots, slashes, or other characters (most notably the
    absolute paths Codex uses under ``[projects.<...>]``) must be emitted
    as quoted strings so the resulting TOML stays parseable.
    """
    if _BARE_KEY_RE.match(key):
        return key
    return '"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _format_toml_header(path: Tuple[str, ...]) -> str:
    return "[" + ".".join(_format_toml_key(part) for part in path) + "]"


def _format_toml_array_header(path: Tuple[str, ...]) -> str:
    return "[[" + ".".join(_format_toml_key(part) for part in path) + "]]"


def _is_table_array(value: Any) -> bool:
    """A non-empty list whose items are all dicts is a TOML array-of-tables."""
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def _dump_toml_value(value: Any) -> str:
    """Serialize a single scalar value back to TOML. Tables handled separately."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        return "[" + ", ".join(_dump_toml_value(item) for item in value) + "]"
    # Fallback: serialize as JSON-ish string (best-effort for unexpected types).
    return _dump_toml_value(json.dumps(value))


def _dump_toml_table(data: Dict[str, Any], path: Tuple[str, ...], lines: List[str]) -> None:
    """Render *data* as a TOML table rooted at *path*, recursing into subtables.

    The split between scalars / subtables / array-of-tables mirrors what
    ``tomllib`` parses, so the rewrite is loss-less for arbitrary
    Codex-shaped configs:

    - scalar leaves under this path are emitted first, under the
      ``[path]`` header (or at the top of the file when ``path`` is empty);
    - dict children become standalone ``[path.subkey]`` tables, recursed
      into so deeper nesting like ``[a.b.c]`` round-trips;
    - lists of dicts become ``[[path.subkey]]`` array-of-tables entries.
    """
    scalars: List[Tuple[str, Any]] = []
    subtables: List[Tuple[str, Dict[str, Any]]] = []
    table_arrays: List[Tuple[str, List[Dict[str, Any]]]] = []
    for key, value in data.items():
        if isinstance(value, dict):
            subtables.append((key, value))
        elif _is_table_array(value):
            table_arrays.append((key, value))
        else:
            scalars.append((key, value))

    if path:
        # Emit ``[path]`` when this table has its own scalars, or when it
        # is otherwise empty (no children) — without the header, an empty
        # leaf disappears entirely from the round-trip. Pure container
        # tables (no scalars, but with subtables) are implicit in TOML:
        # ``[a.b]`` is enough to introduce ``a``.
        if scalars or (not subtables and not table_arrays):
            if lines:
                lines.append("")
            lines.append(_format_toml_header(path))
            for key, value in scalars:
                lines.append(f"{_format_toml_key(key)} = {_dump_toml_value(value)}")
    else:
        for key, value in scalars:
            lines.append(f"{_format_toml_key(key)} = {_dump_toml_value(value)}")

    for key, value in subtables:
        _dump_toml_table(value, path + (key,), lines)

    for key, items in table_arrays:
        sub_path = path + (key,)
        for item in items:
            if lines:
                lines.append("")
            lines.append(_format_toml_array_header(sub_path))
            item_scalars: List[Tuple[str, Any]] = []
            item_subtables: List[Tuple[str, Dict[str, Any]]] = []
            for ik, iv in item.items():
                if isinstance(iv, dict):
                    item_subtables.append((ik, iv))
                else:
                    item_scalars.append((ik, iv))
            for ik, iv in item_scalars:
                lines.append(f"{_format_toml_key(ik)} = {_dump_toml_value(iv)}")
            for ik, iv in item_subtables:
                _dump_toml_table(iv, sub_path + (ik,), lines)


def _dump_toml(data: Dict[str, Any]) -> str:
    """Emit *data* as TOML.

    Comments and original key ordering are lost (Python dicts preserve
    insertion order, so the rewrite is stable round-trip for a single
    parse → mutate → re-emit cycle). Everything else — quoted keys,
    arbitrary nesting depth, arrays of tables — is preserved so saving
    Codex auth never silently drops unrelated config blocks.
    """
    lines: List[str] = []
    _dump_toml_table(data, (), lines)
    return "\n".join(lines) + ("\n" if lines else "")


def _atomic_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    try:
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # pragma: no cover - best effort cleanup
                pass
    try:
        path.chmod(mode)
    except OSError as exc:  # pragma: no cover - non-POSIX
        logger.debug("chmod %s failed: %s", path, exc)


def apply_codex_auth(
    *,
    auth_mode: str,
    api_key: Optional[str],
    base_url: Optional[str],
    home: Path | None = None,
) -> None:
    """Persist the requested auth mode into Codex's on-disk config files.

    - ``api_key`` mode: write ``OPENAI_API_KEY`` into ``auth.json``,
      optionally set ``[model_providers.openai].base_url`` if a non-default
      URL was supplied, and pin top-level ``model_provider = "openai"`` so
      Codex actually uses the keyed provider.
    - ``oauth`` mode: drop ``OPENAI_API_KEY`` from ``auth.json``, leave any
      ``tokens`` blob in place, and clear our managed ``base_url`` so the
      next launch goes back to OpenAI's default endpoint.
    """
    if auth_mode not in {"oauth", "api_key"}:
        raise ValueError(f"Unsupported codex auth_mode: {auth_mode!r}")

    config_path, auth_path = get_codex_config_paths(home)
    auth_data = _load_auth(auth_path)
    toml_data = _load_toml(config_path)

    providers = toml_data.setdefault("model_providers", {})
    if not isinstance(providers, dict):
        providers = {}
        toml_data["model_providers"] = providers
    managed = providers.setdefault(MANAGED_PROVIDER_ID, {})
    if not isinstance(managed, dict):
        managed = {}
        providers[MANAGED_PROVIDER_ID] = managed

    if auth_mode == "api_key":
        if not api_key:
            raise ValueError("api_key is required when auth_mode='api_key'")
        auth_data["OPENAI_API_KEY"] = api_key
        toml_data["model_provider"] = MANAGED_PROVIDER_ID
        managed.setdefault("name", "OpenAI")
        if base_url:
            managed["base_url"] = base_url
        else:
            managed.pop("base_url", None)
    else:  # oauth
        auth_data.pop("OPENAI_API_KEY", None)
        # Leave model_provider as-is — switching back to ChatGPT/OAuth is
        # the user's responsibility via ``codex login``; we just stop
        # pinning the keyed provider's overrides.
        managed.pop("base_url", None)
        # If our managed entry is now empty, drop it entirely so we don't
        # leave a noisy ``[model_providers.openai]`` table behind.
        if not managed:
            providers.pop(MANAGED_PROVIDER_ID, None)
            if not providers:
                toml_data.pop("model_providers", None)

    _atomic_write(auth_path, json.dumps(auth_data, indent=2) + "\n", mode=0o600)
    _atomic_write(config_path, _dump_toml(toml_data), mode=0o600)


def read_codex_auth_state(home: Path | None = None) -> Dict[str, Any]:
    """Return the user-visible auth state for the Settings UI.

    Reads both files and reports back what the user would see — no
    secrets in the response (the UI receives the key length, never the
    plaintext key).
    """
    config_path, auth_path = get_codex_config_paths(home)
    auth_data = _load_auth(auth_path)
    toml_data = _load_toml(config_path)
    api_key = auth_data.get("OPENAI_API_KEY")
    has_chatgpt_tokens = isinstance(auth_data.get("tokens"), dict)

    providers = toml_data.get("model_providers")
    base_url: Optional[str] = None
    if isinstance(providers, dict):
        managed = providers.get(MANAGED_PROVIDER_ID)
        if isinstance(managed, dict):
            raw = managed.get("base_url")
            if isinstance(raw, str) and raw.strip():
                base_url = raw.strip()

    inferred_mode = "api_key" if isinstance(api_key, str) and api_key else "oauth"
    return {
        "auth_mode": inferred_mode,
        "has_api_key": isinstance(api_key, str) and bool(api_key),
        "api_key_length": len(api_key) if isinstance(api_key, str) else 0,
        "base_url": base_url,
        "has_chatgpt_tokens": has_chatgpt_tokens,
    }
