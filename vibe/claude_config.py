"""Helpers for inspecting Claude Code's on-disk configuration.

Unlike Codex, Claude Code's API-key auth is realized purely via environment
variables — the Anthropic SDK honors ``ANTHROPIC_API_KEY`` (or
``ANTHROPIC_AUTH_TOKEN`` for relay setups) and ``ANTHROPIC_BASE_URL`` at
invocation time. ``core/handlers/session_handler.py`` already injects these
from ``V2Config.agents.claude`` before each one-shot CLI launch.

That means V2Config is our sole *writer*; we do **not** mutate
``~/.claude/settings.json``. But the Claude CLI itself reads that file at
launch and applies its ``env`` block on top of the inherited env — so a
hand-edited ``env.ANTHROPIC_AUTH_TOKEN`` there will override whatever we
just injected. The Settings UI needs to surface that conflict instead of
silently letting the user save a value that won't take effect.

This module is therefore read-only:

- ``read_claude_settings_env(...)`` returns whichever ``ANTHROPIC_*`` keys
  are present in ``~/.claude/settings.json`` so the UI can warn the user.
- ``read_claude_auth_state(...)`` rolls that up into the user-visible
  shape consumed by ``vibe.api.get_claude_auth``.

OAuth tokens minted by ``claude login`` live in the macOS keychain (or
the OS-specific equivalent), not on disk. We have no portable way to
inspect them, so the OAuth-signed-in signal is purely an inference from
"no API key is configured" plus "the CLI is reachable".
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Keys we recognise inside ``~/.claude/settings.json``'s ``env`` block.
# ``ANTHROPIC_API_KEY`` is the SDK's documented variable; relay setups
# (e.g. Cloudflare-fronted gateways like ai-relay) prefer
# ``ANTHROPIC_AUTH_TOKEN``. We surface both so a hand-edited config
# doesn't silently invalidate the Settings UI.
RELEVANT_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
)


def get_claude_home(home: Path | None = None) -> Path:
    """Resolve the directory Claude Code reads ``settings.json`` from.

    Claude Code respects ``CLAUDE_CONFIG_DIR`` first (newer builds),
    falling back to ``~/.claude``. We mirror that precedence so the
    Settings UI reports on whichever directory the live CLI actually
    consults.
    """
    if home is not None:
        return home / ".claude"
    env_home = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".claude"


def get_claude_settings_path(home: Path | None = None) -> Path:
    """Return the absolute path to ``~/.claude/settings.json``."""
    return get_claude_home(home) / "settings.json"


def get_claude_credentials_path(home: Path | None = None) -> Path:
    """Return the absolute path to ``~/.claude/credentials.json``.

    The Claude CLI writes OAuth tokens here on platforms that lack a
    usable keychain (notably Linux/Docker, including the regression
    container). The Settings UI uses presence + a token field as a
    best-effort signal for "Claude is signed in via OAuth" since the
    macOS keychain is not portably introspectable.
    """
    return get_claude_home(home) / "credentials.json"


def read_claude_oauth_signed_in(home: Path | None = None) -> bool:
    """Best-effort probe for whether Claude has a usable OAuth session.

    True iff ``~/.claude/credentials.json`` exists and carries something
    that looks like an OAuth token bundle. We don't attempt to introspect
    keychain-backed installs (macOS) — those return False here but the UI
    can still light up the OAuth banner after a successful in-app login.
    """
    path = get_claude_credentials_path(home)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    # Claude writes nested ``claudeAiOauth`` payloads in newer builds; flat
    # ``access_token``/``refresh_token`` keys also occur. Accept either.
    nested = data.get("claudeAiOauth") if isinstance(data.get("claudeAiOauth"), dict) else None
    if nested and any(
        isinstance(nested.get(field), str) and nested.get(field)
        for field in ("access_token", "refresh_token", "accessToken", "refreshToken")
    ):
        return True
    for field in ("access_token", "refresh_token", "accessToken", "refreshToken"):
        if isinstance(data.get(field), str) and data.get(field):
            return True
    return False


def _load_settings(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("Claude settings.json parse failed (%s)", exc)
    return {}


def read_claude_settings_env(home: Path | None = None) -> Dict[str, str]:
    """Extract Anthropic-relevant env vars from ``~/.claude/settings.json``.

    Returns the mapping verbatim (no length truncation, no masking) so
    callers can compute presence + length. The caller is responsible for
    redacting before forwarding to the UI.
    """
    settings = _load_settings(get_claude_settings_path(home))
    env_block = settings.get("env")
    if not isinstance(env_block, dict):
        return {}
    out: Dict[str, str] = {}
    for key in RELEVANT_ENV_KEYS:
        raw = env_block.get(key)
        if isinstance(raw, str) and raw.strip():
            out[key] = raw.strip()
    return out


def read_claude_auth_state(home: Path | None = None) -> Dict[str, Any]:
    """Return the user-visible Claude auth state for the Settings UI.

    Reports on ``~/.claude/settings.json`` only — V2Config is layered on
    top by ``vibe.api.get_claude_auth``. Secrets never leave the server;
    we surface key length + a "settings.json conflict" flag.

    ``settings_env_has_key`` is true when settings.json carries either
    ``ANTHROPIC_API_KEY`` or ``ANTHROPIC_AUTH_TOKEN``. When this is true
    *and* V2Config also has a key, the Settings UI must warn that
    settings.json wins at launch (Claude Code applies its env block on
    top of whatever we inject). ``settings_path`` is forwarded so the
    warning can name the file the user needs to edit.
    """
    env_block = read_claude_settings_env(home)
    settings_path = get_claude_settings_path(home)

    settings_key = env_block.get("ANTHROPIC_API_KEY") or env_block.get(
        "ANTHROPIC_AUTH_TOKEN"
    )
    settings_base = env_block.get("ANTHROPIC_BASE_URL")
    return {
        "settings_path": str(settings_path),
        "settings_exists": settings_path.exists(),
        "settings_env_has_key": bool(settings_key),
        "settings_env_key_length": len(settings_key) if settings_key else 0,
        "settings_env_key_var": (
            "ANTHROPIC_API_KEY"
            if "ANTHROPIC_API_KEY" in env_block
            else ("ANTHROPIC_AUTH_TOKEN" if "ANTHROPIC_AUTH_TOKEN" in env_block else None)
        ),
        "settings_env_base_url": settings_base,
    }


def read_claude_api_key_from_settings(home: Path | None = None) -> Optional[str]:
    """Return ``settings.json``'s ``ANTHROPIC_API_KEY`` if it has one.

    Used as a fallback when the UI sends a base-URL-only update: V2Config
    may be stale (older installs lacked ``api_key``), but the CLI still
    picks up whatever is in ``settings.json``. Prefer that over silently
    blanking the live key.

    Restricted to ``ANTHROPIC_API_KEY`` on purpose. ``ANTHROPIC_AUTH_TOKEN``
    is the bearer-token relay variant — Claude Code applies it from
    ``settings.json`` directly, and our ``api_key`` field always injects
    ``ANTHROPIC_API_KEY`` at launch (see ``session_handler``). Pulling an
    auth-token value into V2Config.api_key would silently switch the
    header semantics on the next save and break bearer-token gateways.
    Bearer-token users should rely on the existing settings.json path or
    re-enter their key into the form.
    """
    env_block = read_claude_settings_env(home)
    return env_block.get("ANTHROPIC_API_KEY")


def build_claude_subprocess_env(
    claude_cfg: Any,
    base_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build the env dict passed to every Claude subprocess / SDK client.

    Both ``core/handlers/session_handler.py`` (one-shot CLI launches) and
    ``core/agent_auth_service.py`` (control-channel SDK clients) need the
    same Anthropic/Claude env composition: inherit relevant vars from the
    parent process, then let V2Config's ``auth_mode`` / ``api_key`` /
    ``base_url`` overrides win.

    The ``auth_mode`` toggle is the load-bearing piece — if a user picks
    OAuth in Settings but their shell exports ``ANTHROPIC_API_KEY``, the
    Claude CLI silently keeps API-key auth and never reaches
    ``~/.claude/credentials.json``. Stripping both ``ANTHROPIC_API_KEY``
    and ``ANTHROPIC_AUTH_TOKEN`` (header-semantics switch) in OAuth mode
    makes the Settings toggle authoritative.

    Centralising the logic prevents the next env-var path (control
    client, future MCP launch, etc.) from forgetting the strip and
    re-introducing the same bug.
    """

    env_source = base_env if base_env is not None else os.environ
    claude_env: Dict[str, str] = {}
    for key, value in env_source.items():
        if key.startswith("ANTHROPIC_") or key.startswith("CLAUDE_"):
            claude_env[key] = value

    if claude_cfg is None:
        return claude_env

    auth_mode = getattr(claude_cfg, "auth_mode", "oauth")
    if auth_mode == "oauth":
        # OAuth means Claude Code reads credentials from
        # ``~/.claude/credentials.json``; any inherited API-key / bearer-
        # token var would suppress that path. Strip both.
        claude_env.pop("ANTHROPIC_API_KEY", None)
        claude_env.pop("ANTHROPIC_AUTH_TOKEN", None)
    elif auth_mode == "api_key":
        configured_key = (getattr(claude_cfg, "api_key", None) or "").strip()
        if configured_key:
            claude_env["ANTHROPIC_API_KEY"] = configured_key
            # When we set an explicit API key, drop any inherited bearer
            # token so the SDK can't pick the wrong Authorization header.
            claude_env.pop("ANTHROPIC_AUTH_TOKEN", None)

    configured_base = (getattr(claude_cfg, "base_url", None) or "").strip()
    if configured_base:
        claude_env["ANTHROPIC_BASE_URL"] = configured_base

    return claude_env
