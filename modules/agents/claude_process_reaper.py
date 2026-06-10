"""Best-effort cleanup for duplicate Claude Code resume processes."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from dataclasses import dataclass

KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


@dataclass(frozen=True)
class ClaudeProcessRow:
    pid: int
    ppid: int
    command: str


def get_claude_client_pid(client: object | None) -> int | None:
    """Return the SDK-managed Claude CLI pid when the current SDK exposes it."""
    transport = getattr(client, "_transport", None)
    process = getattr(transport, "_process", None)
    pid = getattr(process, "pid", None)
    return pid if isinstance(pid, int) and pid > 0 else None


def _run_ps() -> str:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="],
        check=False,
        capture_output=True,
        text=True,
        timeout=1.5,
    )
    return result.stdout or ""


def _parse_ps_rows(output: str) -> list[ClaudeProcessRow]:
    rows: list[ClaudeProcessRow] = []
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        rows.append(ClaudeProcessRow(pid=pid, ppid=ppid, command=parts[2]))
    return rows


def _command_has_resume(command: str, native_session_id: str) -> bool:
    parts = command.split()
    for index, part in enumerate(parts):
        if part == "--resume" and index + 1 < len(parts) and parts[index + 1] == native_session_id:
            return True
        if part.startswith("--resume=") and part.removeprefix("--resume=") == native_session_id:
            return True
    return False


def _command_is_claude(command: str) -> bool:
    parts = command.split()
    if not parts:
        return False
    executable = os.path.basename(parts[0])
    return executable in {"claude", "claude.exe"}


def find_claude_resume_processes(native_session_id: str) -> list[ClaudeProcessRow]:
    """Find Claude Code CLI processes for one native ``--resume`` id."""
    if os.name == "nt" or not native_session_id:
        return []
    try:
        rows = _parse_ps_rows(_run_ps())
    except Exception:
        return []
    return [
        row
        for row in rows
        if row.pid != os.getpid()
        and _command_is_claude(row.command)
        and _command_has_resume(row.command, native_session_id)
    ]


def _descendant_pids(rows: list[ClaudeProcessRow], root_pid: int) -> set[int]:
    children: dict[int, list[int]] = {}
    for row in rows:
        children.setdefault(row.ppid, []).append(row.pid)

    descendants: set[int] = set()
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        if pid in descendants:
            continue
        descendants.add(pid)
        stack.extend(children.get(pid, []))
    return descendants


def _signal_pid(pid: int, sig: int, logger: logging.Logger) -> bool:
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return True
    except Exception:
        logger.debug("Failed to signal Claude duplicate pid=%s signal=%s", pid, sig, exc_info=True)
        return False


async def reap_duplicate_claude_resume_processes(
    native_session_id: str | None,
    *,
    keep_pid: int | None = None,
    logger: logging.Logger,
    terminate_timeout: float = 2.0,
) -> int:
    """Terminate duplicate Claude Code CLI processes for one native session.

    This is intentionally conservative: it only matches a full ``--resume`` id
    and only reaps when more than one matching process exists, or when the
    caller no longer has a tracked PID to keep.
    """
    if not native_session_id or os.name == "nt":
        return 0

    try:
        all_rows = _parse_ps_rows(_run_ps())
    except Exception:
        logger.debug("Failed to read process table for Claude duplicate cleanup", exc_info=True)
        return 0

    matches = [
        row
        for row in all_rows
        if row.pid != os.getpid()
        and _command_is_claude(row.command)
        and _command_has_resume(row.command, native_session_id)
    ]
    if not matches:
        return 0

    keep_pid = keep_pid if isinstance(keep_pid, int) and keep_pid > 0 else None
    target_rows = [row for row in matches if row.pid != keep_pid]
    if keep_pid is not None and len(matches) <= 1:
        return 0
    if not target_rows:
        return 0

    target_pids = {row.pid for row in target_rows}
    for row in target_rows:
        target_pids.update(_descendant_pids(all_rows, row.pid))
    target_pids.discard(os.getpid())
    if keep_pid is not None:
        target_pids.discard(keep_pid)

    if not target_pids:
        return 0

    logger.warning(
        "Reaping %d duplicate Claude resume process(es) for native session %s (keep_pid=%s)",
        len(target_pids),
        native_session_id,
        keep_pid,
    )
    for pid in sorted(target_pids):
        _signal_pid(pid, signal.SIGTERM, logger)

    deadline = asyncio.get_running_loop().time() + terminate_timeout
    remaining = set(target_pids)
    while remaining and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)
        for pid in list(remaining):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                remaining.discard(pid)
            except Exception:
                remaining.discard(pid)

    for pid in sorted(remaining):
        _signal_pid(pid, KILL_SIGNAL, logger)

    return len(target_pids)
