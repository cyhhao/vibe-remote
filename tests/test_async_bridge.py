import asyncio

import pytest

from vibe.async_bridge import run_coroutine_blocking


async def _value():
    return 42


def test_run_coroutine_blocking_allows_sync_callers():
    assert run_coroutine_blocking(_value()) == 42


def test_run_coroutine_blocking_rejects_active_event_loop():
    async def _exercise():
        with pytest.raises(RuntimeError, match="active event loop"):
            run_coroutine_blocking(_value())

    asyncio.run(_exercise())
