"""System proxy detection utilities.

Provides helpers that detect the OS-level proxy configuration and return
values usable by Python networking libraries (aiohttp, websockets, etc.).
"""

import logging
import os
import subprocess
from typing import Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def redact_proxy_url(proxy_url: Optional[str]) -> str:
    """Return a proxy URL safe for logs (userinfo stripped).

    Proxy URLs commonly include ``user:password@`` credentials. Logging them
    verbatim leaks secrets into ``~/.vibe_remote/logs/``. This helper keeps
    scheme, host, and port so operators can still identify the target, but
    drops anything that could contain a credential.
    """
    if not proxy_url:
        return "<unset>"
    try:
        parts = urlsplit(proxy_url)
        if parts.scheme and parts.hostname:
            host = parts.hostname
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            if parts.port:
                return f"{parts.scheme}://{host}:{parts.port}"
            return f"{parts.scheme}://{host}"
    except Exception:
        pass
    return "<configured>"


def resolve_proxy(config_proxy: Optional[str]) -> Optional[str]:
    """Resolve the effective proxy URL for an IM adapter.

    Returns the explicit ``config_proxy`` when set, otherwise falls back to
    the system SOCKS proxy via :func:`get_system_socks_proxy`. Returns
    ``None`` when neither is configured.

    This is the single decision point for "what proxy should this platform
    use?" so adapters do not need to re-implement the precedence rule.
    """
    if config_proxy and config_proxy.strip():
        return config_proxy.strip()
    return get_system_socks_proxy()


def get_system_socks_proxy() -> Optional[str]:
    """Return the system SOCKS proxy URL, or ``None`` if not configured.

    Checks, in order:
    1. ``ALL_PROXY`` / ``all_proxy`` environment variable.
    2. ``HTTPS_PROXY`` / ``https_proxy`` (if it's a socks URL).
    3. macOS ``networksetup -getsocksfirewallproxy Wi-Fi``.
    """
    for var in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy"):
        val = os.environ.get(var, "")
        if val and "socks" in val.lower():
            return val

    # macOS: query system SOCKS proxy
    try:
        out = subprocess.run(
            ["networksetup", "-getsocksfirewallproxy", "Wi-Fi"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode == 0:
            lines = {
                k.strip().lower(): v.strip()
                for line in out.stdout.splitlines()
                if ":" in line
                for k, v in [line.split(":", 1)]
            }
            if lines.get("enabled") == "Yes":
                host = lines.get("server", "127.0.0.1")
                port = lines.get("port", "1080")
                url = f"socks5://{host}:{port}"
                logger.debug("Detected macOS SOCKS proxy: %s", url)
                return url
    except Exception:
        pass

    return None
