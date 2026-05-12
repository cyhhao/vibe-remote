"""Lightweight process diagnostics for shutdown investigations."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Callable


def _run_ps(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["ps", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.5,
        )
    except Exception as exc:
        return f"<ps failed: {exc}>"
    output = (result.stdout or result.stderr or "").strip()
    if not output:
        return "<none>"
    return output


def process_identity(pid: int | None = None) -> dict[str, int | None]:
    """Return best-effort POSIX process identity fields."""
    target_pid = pid if pid is not None else os.getpid()
    identity: dict[str, int | None] = {"pid": target_pid}

    if target_pid == os.getpid():
        identity["ppid"] = os.getppid()
        identity["pgid"] = os.getpgrp() if hasattr(os, "getpgrp") else None
        identity["sid"] = os.getsid(0) if hasattr(os, "getsid") else None
        return identity

    identity["ppid"] = None
    try:
        identity["pgid"] = os.getpgid(target_pid) if hasattr(os, "getpgid") else None
    except ProcessLookupError:
        identity["pgid"] = None
    except Exception:
        identity["pgid"] = None
    try:
        identity["sid"] = os.getsid(target_pid) if hasattr(os, "getsid") else None
    except ProcessLookupError:
        identity["sid"] = None
    except Exception:
        identity["sid"] = None
    return identity


def process_row(pid: int) -> str:
    return _run_ps(["-p", str(pid), "-o", "pid=,ppid=,pgid=,sess=,stat=,command="])


def _process_table_rows(predicate: Callable[[list[str]], bool], *, limit: int) -> tuple[int, list[str]]:
    output = _run_ps(["-axo", "pid=,ppid=,pgid=,sess=,stat=,command="])
    if output.startswith("<"):
        return 0, [output]

    rows: list[str] = []
    total = 0
    for line in output.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        if not predicate(parts):
            continue
        total += 1
        if len(rows) < limit:
            rows.append(line.strip())
    return total, rows


def _format_rows(total: int, rows: list[str]) -> str:
    if not rows:
        return "<none>"
    suffix = "" if total <= len(rows) else f" ... (+{total - len(rows)} more)"
    return " | ".join(rows) + suffix


def log_process_snapshot(
    logger: logging.Logger,
    reason: str,
    *,
    pid: int | None = None,
    limit: int = 30,
) -> None:
    """Log a compact process snapshot around service lifecycle events."""
    if not logger.isEnabledFor(logging.INFO):
        return

    target_pid = pid if pid is not None else os.getpid()
    identity = process_identity(target_pid)
    own_pid = os.getpid()
    own_pgid = os.getpgrp() if hasattr(os, "getpgrp") else None
    target_pgid = identity.get("pgid")

    logger.info(
        "Process snapshot (%s): pid=%s ppid=%s pgid=%s sid=%s service_pid=%s service_pgid=%s",
        reason,
        identity.get("pid"),
        identity.get("ppid"),
        target_pgid,
        identity.get("sid"),
        own_pid,
        own_pgid,
    )

    parent_pid = identity.get("ppid")
    if parent_pid:
        logger.info("Process snapshot (%s) parent: %s", reason, process_row(parent_pid))

    logger.info("Process snapshot (%s) target: %s", reason, process_row(target_pid))

    if target_pgid is not None:
        total, rows = _process_table_rows(lambda parts: parts[2] == str(target_pgid), limit=limit)
        logger.info(
            "Process snapshot (%s) pgid=%s members (%s): %s",
            reason,
            target_pgid,
            total,
            _format_rows(total, rows),
        )

    total, rows = _process_table_rows(lambda parts: parts[1] == str(target_pid), limit=limit)
    logger.info(
        "Process snapshot (%s) direct children (%s): %s",
        reason,
        total,
        _format_rows(total, rows),
    )
