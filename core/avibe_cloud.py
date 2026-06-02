"""Avibe Cloud public-URL availability — the lightweight, storage-free slice the
handler / agent / system-prompt paths need WITHOUT dragging the show-page storage
layer (and sqlite) into their import path.

``core.show_pages`` imports ``storage.db`` / SQLAlchemy at module load to back
``ShowPageStore``. Lightweight callers (``core.handlers.session_handler``,
``core.system_prompt_injection``, the Codex / OpenCode agent backends) only ever
needed "is the avibe.bot public URL configured, and the connect guidance if not"
— so importing ``core.show_pages`` for that one helper transitively pulled sqlite
onto the agent-setup / command-handler import path. These primitives are derived
purely from ``V2Config`` (no storage), so they live here; ``core.show_pages``
builds the show-page URLs + ``ShowPageStore`` on top of ``base_public_url``.

Guarded by ``tests/test_native_session_providers.py::
test_native_session_lightweight_imports_do_not_require_sqlite``.
"""

from __future__ import annotations

from config.v2_config import V2Config

AVIBE_CLOUD_CONNECT_GUIDANCE = (
    "⚠️ Avibe Cloud is not connected, so this page cannot be accessed from the public internet "
    "through your domain. To fully use Show Pages, register an avibe.bot account, claim your dedicated "
    "domain and pairing key, then run `vibe remote pair`."
)


def base_public_url(config: V2Config | None = None) -> str | None:
    """The configured avibe.bot public base URL (trailing slash stripped), or
    ``None`` when Avibe Cloud is disabled / unconfigured / unreadable."""
    try:
        cfg = config or V2Config.load()
    except Exception:
        return None
    cloud = getattr(getattr(cfg, "remote_access", None), "vibe_cloud", None)
    if not cloud or not getattr(cloud, "enabled", False):
        return None
    public_url = (getattr(cloud, "public_url", "") or "").strip()
    return public_url.rstrip("/") if public_url else None


def avibe_cloud_url_available(config: V2Config | None = None) -> bool:
    return bool(base_public_url(config))


def avibe_cloud_connect_guidance(config: V2Config | None = None) -> str | None:
    return None if avibe_cloud_url_available(config) else AVIBE_CLOUD_CONNECT_GUIDANCE
