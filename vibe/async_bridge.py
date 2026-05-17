"""Compatibility helpers for legacy sync call sites."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import TypeVar


T = TypeVar("T")


def run_coroutine_blocking(coro: Coroutine[object, object, T]) -> T:
    """Run a coroutine from legacy synchronous code.

    New UI request handlers should await directly on the ASGI event loop. This
    helper exists for old tests and non-request callers that have not migrated
    yet. When already inside an event loop, the coroutine is isolated in a short
    helper must not be used from async UI handlers; those should call the
    corresponding ``*_async`` function directly.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    coro.close()
    raise RuntimeError("run_coroutine_blocking cannot be used from an active event loop")
