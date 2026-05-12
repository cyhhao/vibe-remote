"""Helpers for isolating and terminating managed child processes."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from typing import Any

KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


def isolated_subprocess_kwargs() -> dict[str, Any]:
    """Return subprocess kwargs that put a child outside this process group."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _safe_signal_process_group(pid: int, sig: int, logger: logging.Logger, label: str) -> bool:
    if os.name == "nt" or not hasattr(os, "getpgid") or not hasattr(os, "killpg"):
        return False
    try:
        pgid = os.getpgid(pid)
        own_pgid = os.getpgrp()
    except ProcessLookupError:
        return True
    except Exception:
        logger.debug("Failed to inspect process group for %s pid=%s", label, pid, exc_info=True)
        return False
    if pgid == own_pgid:
        logger.error(
            "Refusing to signal %s process group for pid=%s because it matches the Vibe Remote service pgid=%s",
            label,
            pid,
            own_pgid,
        )
        return False
    try:
        os.killpg(pgid, sig)
        return True
    except ProcessLookupError:
        return True
    except Exception:
        logger.debug("Failed to signal %s process group pgid=%s", label, pgid, exc_info=True)
        return False


def signal_process_tree(process: Any, sig: int, logger: logging.Logger, label: str) -> None:
    """Signal a managed process group, falling back to the direct process."""
    pid = getattr(process, "pid", None)
    if isinstance(pid, int) and _safe_signal_process_group(pid, sig, logger, label):
        return

    try:
        if sig == signal.SIGTERM:
            process.terminate()
        elif sig == KILL_SIGNAL:
            process.kill()
        else:
            process.send_signal(sig)
    except ProcessLookupError:
        return


async def terminate_process_tree(
    process: Any,
    logger: logging.Logger,
    label: str,
    *,
    terminate_timeout: float = 3.0,
) -> None:
    """Terminate a managed subprocess without signaling the service group."""
    if getattr(process, "returncode", None) is not None:
        return

    signal_process_tree(process, signal.SIGTERM, logger, label)
    try:
        await asyncio.wait_for(process.wait(), timeout=terminate_timeout)
        return
    except asyncio.TimeoutError:
        pass

    signal_process_tree(process, KILL_SIGNAL, logger, label)
    try:
        await process.wait()
    except ProcessLookupError:
        return


async def terminate_and_communicate(
    process: Any,
    logger: logging.Logger,
    label: str,
    *,
    terminate_timeout: float = 3.0,
) -> tuple[bytes, bytes]:
    """Terminate a process tree and drain stdout/stderr."""
    if getattr(process, "returncode", None) is None:
        signal_process_tree(process, signal.SIGTERM, logger, label)
    try:
        return await asyncio.wait_for(process.communicate(), timeout=terminate_timeout)
    except asyncio.TimeoutError:
        signal_process_tree(process, KILL_SIGNAL, logger, label)
        return await process.communicate()
