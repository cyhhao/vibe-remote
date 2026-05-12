import logging

from core import process_diagnostics


def test_log_process_snapshot_includes_recursive_descendants_and_related(caplog, monkeypatch):
    table = "\n".join(
        [
            "100 1 100 100 Ss /usr/bin/python main.py",
            "200 100 200 200 Ss node codex --dangerously-bypass-approvals-and-sandbox app-server",
            "201 200 200 200 S /vendor/codex app-server",
            "300 1 300 300 S claude",
        ]
    )

    def fake_run_ps(args):
        if args[:2] == ["-p", "100"]:
            return "100 1 100 100 Ss /usr/bin/python main.py"
        if args[:2] == ["-p", "1"]:
            return "1 0 1 1 Ss launchd"
        if args[:2] == ["-axo", "pid=,ppid=,pgid=,sess=,stat=,command="]:
            return table
        return "<none>"

    monkeypatch.setattr(process_diagnostics, "_run_ps", fake_run_ps)
    monkeypatch.setattr(
        process_diagnostics,
        "process_identity",
        lambda pid=None: {"pid": 100, "ppid": 1, "pgid": 100, "sid": 100},
    )

    logger = logging.getLogger("test.process_diagnostics")
    with caplog.at_level(logging.INFO, logger=logger.name):
        process_diagnostics.log_process_snapshot(
            logger,
            "test",
            pid=100,
            related_terms=("codex app-server", "claude"),
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "Process snapshot (test) descendants (2):" in messages
    assert "200 100 200 200 Ss node codex" in messages
    assert "201 200 200 200 S /vendor/codex app-server" in messages
    assert "Process snapshot (test) related processes (2):" in messages
    assert "300 1 300 300 S claude" in messages
