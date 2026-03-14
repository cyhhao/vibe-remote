"""System proxy detection utilities.

Provides helpers that detect the OS-level proxy configuration and return
values usable by Python networking libraries (aiohttp, websockets, etc.).
"""

import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


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
