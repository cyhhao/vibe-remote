from __future__ import annotations

import json

from config import paths
from storage.db import create_sqlite_engine
from storage.importer import ensure_sqlite_state
from storage.models import agent_sessions, messages
from storage.settings_service import upsert_scope
from vibe import cli


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    ensure_sqlite_state(primary_platform="avibe")
    return create_sqlite_engine(paths.get_sqlite_state_path())


def _seed(engine, sid, *, platform="avibe", native="proj_a", title="T", backend="claude", status="active", title_source=None, last_active="2026-06-09T10:00:00Z"):
    now = "2026-06-09T09:00:00Z"
    scope_type = "project" if platform == "avibe" else "channel"
    metadata = {"title_source": title_source} if title_source else {}
    with engine.begin() as conn:
        scope_id = upsert_scope(conn, platform=platform, scope_type=scope_type, native_id=native, now=now)
        conn.execute(
            agent_sessions.insert().values(
                id=sid, scope_id=scope_id, agent_id="agent_internal_" + sid, agent_name=backend,
                agent_backend=backend, agent_variant="default", session_anchor="anc_" + sid,
                native_session_id="nat_" + sid, title=title, status=status, agent_status="idle",
                metadata_json=json.dumps(metadata), created_at=now, updated_at=now, last_active_at=last_active,
            )
        )
    return scope_id


def _run(cmd, argv, capsys):
    args = cli.build_parser().parse_args(argv)
    code = cmd(args)
    captured = capsys.readouterr()
    stream = captured.out if code == 0 else captured.err
    return code, json.loads(stream)


# --------------------------------------------------------------------------- list


def test_list_excludes_archived_and_orders_by_activity(monkeypatch, tmp_path, capsys):
    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesaaa", platform="avibe", native="proj_a", last_active="2026-06-09T12:00:00Z")
    _seed(engine, "sesbbb", platform="slack", native="C1", last_active="2026-06-09T13:00:00Z")
    _seed(engine, "sesarch", platform="avibe", native="proj_a", status="archived", last_active="2026-06-09T14:00:00Z")

    code, payload = _run(cli.cmd_session_list, ["session", "list"], capsys)
    assert code == 0
    assert payload["kind"] == "agent_sessions"
    ids = [s["id"] for s in payload["sessions"]]
    assert ids == ["sesbbb", "sesaaa"]  # newest activity first; archived excluded
    assert "data query" in payload["message"]


def test_list_row_has_only_lean_fields(monkeypatch, tmp_path, capsys):
    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesaaa", platform="slack", native="C1")
    _, payload = _run(cli.cmd_session_list, ["session", "list"], capsys)
    row = payload["sessions"][0]
    assert set(row) == {"id", "title", "platform", "project_id", "agent_name", "agent_status", "last_active_at"}
    assert row["platform"] == "slack"
    assert "agent_backend" not in row and "status" not in row


def test_list_type_filter(monkeypatch, tmp_path, capsys):
    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesweb", platform="avibe", native="proj_a")
    _seed(engine, "seschat", platform="slack", native="C1")
    _, payload = _run(cli.cmd_session_list, ["session", "list", "--type", "slack"], capsys)
    assert [s["id"] for s in payload["sessions"]] == ["seschat"]


def test_list_pagination_fixed_ten_no_limit_flag(monkeypatch, tmp_path, capsys):
    engine = _setup(monkeypatch, tmp_path)
    for i in range(12):
        _seed(engine, f"ses{i:02d}", platform="slack", native="C1", last_active=f"2026-06-09T10:{i:02d}:00Z")
    _, page1 = _run(cli.cmd_session_list, ["session", "list"], capsys)
    assert len(page1["sessions"]) == 10
    assert page1["pagination"]["has_more"] is True
    assert page1["pagination"]["next_command"] == "vibe session list --page 2"
    assert "--limit" not in page1["pagination"]["next_command"]
    _, page2 = _run(cli.cmd_session_list, ["session", "list", "--page", "2"], capsys)
    assert len(page2["sessions"]) == 2
    assert page2["pagination"]["has_more"] is False


def test_list_on_fresh_home_returns_empty(monkeypatch, tmp_path, capsys):
    # No ensure_sqlite_state(): _open_session_engine must bootstrap the DB itself,
    # so a fresh Avibe home returns a clean empty list, not "no such table" (Codex P2).
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    code, payload = _run(cli.cmd_session_list, ["session", "list"], capsys)
    assert code == 0
    assert payload["sessions"] == []


def test_list_invalid_type_errors(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    code, payload = _run(cli.cmd_session_list, ["session", "list", "--type", "bogus"], capsys)
    assert code == 1
    assert payload["ok"] is False
    assert payload["code"] == "invalid_session_type"


# ---------------------------------------------------------------------------- get


def test_get_returns_detail_without_status_agentid_anchor(monkeypatch, tmp_path, capsys):
    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesaaa", platform="avibe", native="proj_a")
    code, payload = _run(cli.cmd_session_get, ["session", "get", "sesaaa"], capsys)
    assert code == 0
    s = payload["session"]
    assert s["id"] == "sesaaa"
    assert s["platform"] == "avibe"
    assert s["agent_backend"] == "claude"  # backend kept in detail
    for omitted in ("status", "agent_id", "session_anchor"):
        assert omitted not in s
    assert "vibe runs list --session-id sesaaa" in payload["message"]


def test_get_archived_is_not_found(monkeypatch, tmp_path, capsys):
    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesarch", status="archived")
    code, payload = _run(cli.cmd_session_get, ["session", "get", "sesarch"], capsys)
    assert code == 1
    assert payload["code"] == "session_not_found"


def test_get_missing_is_not_found(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    code, payload = _run(cli.cmd_session_get, ["session", "get", "nope"], capsys)
    assert code == 1
    assert payload["code"] == "session_not_found"


# ------------------------------------------------------------------------- update


def test_update_sets_title(monkeypatch, tmp_path, capsys):
    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesaaa", title="Old")
    code, payload = _run(cli.cmd_session_update, ["session", "update", "sesaaa", "--title", "New name"], capsys)
    assert code == 0
    assert payload["updated"] is True
    assert payload["session"]["title"] == "New name"
    assert "status" not in payload["session"]


def test_update_empty_title_clears(monkeypatch, tmp_path, capsys):
    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesaaa", title="Old")
    _, payload = _run(cli.cmd_session_update, ["session", "update", "sesaaa", "--title", ""], capsys)
    assert payload["session"]["title"] is None


def test_update_archived_is_not_found(monkeypatch, tmp_path, capsys):
    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesarch", status="archived", title="Old")
    code, payload = _run(cli.cmd_session_update, ["session", "update", "sesarch", "--title", "x"], capsys)
    assert code == 1
    assert payload["code"] == "session_not_found"
    # title must not have been written to the soft-deleted row
    with engine.connect() as conn:
        from sqlalchemy import select

        title = conn.execute(select(agent_sessions.c.title).where(agent_sessions.c.id == "sesarch")).scalar_one()
    assert title == "Old"


# ------------------------------------------------------------------- IM linkage


def test_im_messages_link_to_session(monkeypatch, tmp_path):
    import core.message_mirror as mm
    from modules.im.base import MessageContext
    from sqlalchemy import select

    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesim", platform="slack", native="C777")

    spec = {"agent_session_id": "sesim", "vibe_agent_name": "claude", "vibe_agent_backend": "claude"}
    # agent reply rides the source session_id; scope stays the delivery channel
    mm.persist_agent_message(
        MessageContext(user_id="U", channel_id="C777", platform="slack", thread_id="t1", platform_specific=spec),
        "result", "answer",
    )
    # human inbound is scope-keyed first, then back-filled
    mm.mirror_inbound(MessageContext(user_id="U", channel_id="C777", platform="slack", thread_id="t1", message_id="m1"), "question")
    mm.link_inbound_message_session(platform="slack", native_message_id="m1", session_id="sesim")
    # harness prompt carrying a source session links too
    mm.mirror_harness_inbound(
        MessageContext(user_id="s", channel_id="C777", platform="slack", message_id="h1",
                       platform_specific={"agent_session_id": "sesim", "task_trigger_kind": "scheduled", "task_definition_id": "d1"}),
        "reminder",
    )

    with engine.connect() as conn:
        rows = list(conn.execute(select(messages.c.author, messages.c.session_id)).mappings())
    assert len(rows) == 3
    assert all(r["session_id"] == "sesim" for r in rows)


def test_link_inbound_is_noop_when_already_linked(monkeypatch, tmp_path):
    import core.message_mirror as mm
    from modules.im.base import MessageContext
    from sqlalchemy import select

    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesim", platform="slack", native="C777")
    _seed(engine, "sesother", platform="slack", native="C777")
    mm.mirror_inbound(MessageContext(user_id="U", channel_id="C777", platform="slack", message_id="m1"), "q")
    mm.link_inbound_message_session(platform="slack", native_message_id="m1", session_id="sesim")
    # a second, different back-fill must not overwrite an already-linked row
    mm.link_inbound_message_session(platform="slack", native_message_id="m1", session_id="sesother")
    with engine.connect() as conn:
        sid = conn.execute(select(messages.c.session_id).where(messages.c.native_message_id == "m1")).scalar_one()
    assert sid == "sesim"


# ------------------------------------------------------------ title nudge prompt


def _injection_for(session_id):
    from core.system_prompt_injection import build_system_prompt_injection
    from modules.im.base import MessageContext

    ctx = MessageContext(
        user_id="u", channel_id="c", platform="avibe",
        platform_specific={"agent_session_id": session_id},
    )
    return build_system_prompt_injection(context=ctx)


def test_title_nudge_when_empty(monkeypatch, tmp_path):
    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesnone", title="")
    out = _injection_for("sesnone")
    # the command carries the REAL session id (not a <id> placeholder)
    assert 'vibe session update sesnone --title "<short title>"' in out
    assert "not set yet" in out
    assert "silently" in out  # only update once, without disturbing the user


def test_title_nudge_when_auto_generated(monkeypatch, tmp_path):
    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesauto", title="Some auto title", title_source="backend")
    out = _injection_for("sesauto")
    assert "vibe session update sesauto --title" in out
    assert "auto-generated" in out


def test_no_title_nudge_when_user_set(monkeypatch, tmp_path):
    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sesuser", title="Release review", title_source="user")
    out = _injection_for("sesuser")
    assert "vibe session update sesuser --title" not in out
    assert "Current Session Reminder" in out  # the reminder itself still renders


def test_no_title_nudge_when_user_cleared(monkeypatch, tmp_path):
    # A user who deliberately cleared the title (empty + title_source="user") must
    # NOT be nudged, or the agent would undo the clear next turn (Codex P2).
    engine = _setup(monkeypatch, tmp_path)
    _seed(engine, "sescleared", title="", title_source="user")
    out = _injection_for("sescleared")
    assert "vibe session update sescleared --title" not in out
