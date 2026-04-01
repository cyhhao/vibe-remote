from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_sessions import SessionsStore
from core.controller import Controller
from core.scheduled_tasks import ParsedSessionKey, ScheduledTaskService, ScheduledTaskStore, build_session_key_for_context
from modules.im import MessageContext
from modules.sessions_facade import SessionsFacade


class _ControllerHarness:
    primary_platform = "slack"

    _resolve_session_scope_id = staticmethod(Controller._resolve_session_scope_id)
    _get_settings_key = Controller._get_settings_key
    _get_session_scope_id = Controller._get_session_scope_id
    _get_session_key = Controller._get_session_key
    _get_legacy_session_key = Controller._get_legacy_session_key


def test_controller_uses_dm_channel_for_runtime_session_key() -> None:
    controller = _ControllerHarness()
    context = MessageContext(
        user_id="U123",
        channel_id="D123",
        platform="slack",
        platform_specific={"is_dm": True},
    )

    assert controller._get_settings_key(context) == "U123"
    assert controller._get_session_key(context) == "slack::D123"
    assert controller._get_legacy_session_key(context) == "slack::U123"


def test_dm_default_external_session_key_uses_user_scope() -> None:
    context = MessageContext(
        user_id="U123",
        channel_id="D123",
        platform="slack",
        platform_specific={"is_dm": True},
    )

    assert build_session_key_for_context(context).to_key(include_thread=False) == "slack::user::U123"


def test_sessions_facade_falls_back_to_legacy_dm_scope_and_migrates() -> None:
    store = SessionsStore(sessions_path=Path("/tmp/nonexistent-sessions-fallback.json"))
    facade = SessionsFacade(store)
    facade.set_agent_session_mapping("slack::U123", "codex", "slack_thread-1", "thread-abc")

    session_id = facade.get_agent_session_id_with_fallback(
        "slack::D123",
        "slack::U123",
        "slack_thread-1",
        "codex",
    )

    assert session_id == "thread-abc"
    assert facade.get_agent_session_id("slack::D123", "slack_thread-1", "codex") == "thread-abc"


def test_migrate_active_polls_backfills_dm_runtime_scope(tmp_path: Path) -> None:
    sessions_path = tmp_path / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "active_polls": {
                    "sess-1": {
                        "opencode_session_id": "sess-1",
                        "base_session_id": "slack_171717.123",
                        "channel_id": "D123",
                        "thread_id": "171717.123",
                        "settings_key": "U123",
                        "working_path": "/tmp/work",
                        "user_id": "U123",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    store = SessionsStore(sessions_path=sessions_path)
    store.load()

    store.migrate_active_polls("slack")
    poll = store.get_active_poll("sess-1")

    assert poll is not None
    assert poll.platform == "slack"
    assert poll.is_dm is True
    assert poll.session_scope_key == "D123"


def test_scheduled_dm_context_uses_runtime_dm_channel_scope() -> None:
    settings_manager = SimpleNamespace(
        get_store=lambda: SimpleNamespace(
            get_user=lambda *_args, **_kwargs: SimpleNamespace(dm_chat_id="D123")
        )
    )
    controller = SimpleNamespace(
        platform_settings_managers={"slack": settings_manager},
        get_im_client_for_context=lambda _context: SimpleNamespace(
            should_use_thread_for_reply=lambda: True,
            should_use_thread_for_dm_session=lambda: True,
        ),
        _resolve_session_scope_id=Controller._resolve_session_scope_id,
    )
    service = ScheduledTaskService(controller=controller, store=ScheduledTaskStore(Path("/tmp/nonexistent-scheduled.json")))
    target = ParsedSessionKey(platform="slack", scope_type="user", scope_id="U123")

    context = asyncio.run(service._build_context(target, execution_id="exec-1"))

    assert context.channel_id == "D123"
    assert context.platform_specific["delivery_scope_session_key"] == "slack::D123"
    assert context.platform_specific["scheduled_delivery_alias"]["session_key"] == "slack::D123"
