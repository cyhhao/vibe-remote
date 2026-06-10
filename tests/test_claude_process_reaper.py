import logging
import signal

import pytest

from modules.agents import claude_process_reaper


def test_find_claude_resume_processes_matches_exact_resume_id(monkeypatch):
    table = "\n".join(
        [
            "100 1 /usr/local/bin/claude --resume sess-1 --model opus",
            "101 1 /usr/local/bin/claude --resume sess-10 --model opus",
            "102 1 /usr/local/bin/claude --resume=sess-1 --model opus",
            "103 1 /usr/local/bin/codex --resume sess-1",
            "104 1 /usr/local/bin/not-claude --resume sess-1",
        ]
    )
    monkeypatch.setattr(claude_process_reaper, "_run_ps", lambda: table)

    rows = claude_process_reaper.find_claude_resume_processes("sess-1")

    assert [row.pid for row in rows] == [100, 102]


@pytest.mark.asyncio
async def test_reap_duplicate_claude_resume_processes_kills_matches_and_descendants(monkeypatch):
    table = "\n".join(
        [
            "100 1 /usr/local/bin/claude --resume sess-1 --model opus",
            "101 1 /usr/local/bin/claude --resume sess-1 --model opus",
            "102 101 node helper.js",
            "200 1 /usr/local/bin/claude --resume sess-2 --model opus",
        ]
    )
    signals = []
    alive = {100, 101, 102, 200}

    def fake_kill(pid, sig):
        if sig == 0:
            if pid not in alive:
                raise ProcessLookupError
            return
        signals.append((pid, sig))
        alive.discard(pid)

    monkeypatch.setattr(claude_process_reaper, "_run_ps", lambda: table)
    monkeypatch.setattr(claude_process_reaper.os, "kill", fake_kill)

    reaped = await claude_process_reaper.reap_duplicate_claude_resume_processes(
        "sess-1",
        keep_pid=100,
        logger=logging.getLogger("test.claude_reaper"),
    )

    assert reaped == 2
    assert (101, signal.SIGTERM) in signals
    assert (102, signal.SIGTERM) in signals
    assert all(pid not in (100, 200) for pid, _ in signals)


@pytest.mark.asyncio
async def test_reap_duplicate_claude_resume_processes_keeps_single_tracked_pid(monkeypatch):
    monkeypatch.setattr(
        claude_process_reaper,
        "_run_ps",
        lambda: "100 1 /usr/local/bin/claude --resume sess-1 --model opus",
    )
    signals = []
    monkeypatch.setattr(claude_process_reaper.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    reaped = await claude_process_reaper.reap_duplicate_claude_resume_processes(
        "sess-1",
        keep_pid=100,
        logger=logging.getLogger("test.claude_reaper"),
    )

    assert reaped == 0
    assert signals == []
