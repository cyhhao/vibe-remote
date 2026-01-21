"""Client facade for talking to OpenCode server."""

from __future__ import annotations

import asyncio
from typing import Optional

from .server import OpenCodeServerManager


class OpenCodeClientManager:
    """Lazily initializes and returns a shared OpenCodeServerManager instance."""

    def __init__(self, opencode_config):
        self._config = opencode_config
        self._server_manager: Optional[OpenCodeServerManager] = None
        self._lock = asyncio.Lock()

    async def get_server(self) -> OpenCodeServerManager:
        async with self._lock:
            if self._server_manager is None:
                self._server_manager = await OpenCodeServerManager.get_instance(
                    binary=self._config.binary,
                    port=self._config.port,
                    request_timeout_seconds=self._config.request_timeout_seconds,
                )
            return self._server_manager
