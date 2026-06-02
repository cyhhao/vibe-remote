"""Unit tests for the workbench sidebar-dot status, driven by EXACTLY two
chokepoints (no per-path / per-backend instrumentation):

* inbound  — ``AgentService.handle_message`` marks an avibe session ``running``.
* outbound — ``MessageDispatcher.emit_agent_message`` settles a terminal
  ``result`` to ``idle`` (or ``failed`` when ``is_error``); see
  ``test_message_dispatcher_status``.

This file pins the inbound point + the avibe gating in
``Controller._session_id_from_context`` (only workbench turns carry a session id,
so IM/CLI turns never touch the dot).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.controller import Controller
from modules.agents.service import AgentService


def _ctx(session_id, *, platform="avibe"):
    spec = {"agent_session_id": session_id} if session_id else {}
    return SimpleNamespace(platform=platform, platform_specific=spec)


def test_session_id_from_context_reads_agent_session_id():
    assert Controller._session_id_from_context(_ctx("ses-1")) == "ses-1"
    # IM / CLI turns carry no workbench session id → resolve to None (dot skipped).
    assert Controller._session_id_from_context(SimpleNamespace(platform_specific={})) is None
    assert Controller._session_id_from_context(SimpleNamespace(platform_specific=None)) is None
    assert Controller._session_id_from_context(None) is None


def _service_with_capture():
    calls: list = []
    controller = SimpleNamespace(
        _session_id_from_context=staticmethod(Controller._session_id_from_context).__func__,
        set_agent_status=lambda sid, status: calls.append((sid, status)),
    )
    # The inbound chokepoint now marks running via the turn owner (FSM); wire a real
    # one so on_running reaches this stub's set_agent_status recorder.
    from core.session_turns import SessionTurnManager

    controller.session_turns = SessionTurnManager(controller)
    service = AgentService(controller)
    return service, calls


def test_inbound_marks_running_for_avibe_turn():
    service, calls = _service_with_capture()
    dispatched = []

    async def _handle(req):
        dispatched.append(req)

    service.agents["claude"] = SimpleNamespace(name="claude", handle_message=_handle)
    request = SimpleNamespace(context=_ctx("ses-abc"))

    asyncio.run(service.handle_message("claude", request))

    # Inbound chokepoint flips the dot green, then dispatches to the backend.
    assert calls == [("ses-abc", "running")]
    assert dispatched == [request]


def test_inbound_skips_non_avibe_turn():
    service, calls = _service_with_capture()

    async def _handle(req):
        pass

    service.agents["claude"] = SimpleNamespace(name="claude", handle_message=_handle)
    request = SimpleNamespace(context=_ctx(None, platform="slack"))

    asyncio.run(service.handle_message("claude", request))

    # IM turn carries no workbench session id → the dot is never touched.
    assert calls == []
