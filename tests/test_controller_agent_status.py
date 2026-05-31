"""Unit tests for the controller's agent-status latch.

The ``_sessions_turn_failed`` latch carries the "this turn failed" signal from
the emit points (auth-recovery / error-subtype result) to the turn-end
classification in ``core/internal_server._run_turn``. These tests pin the
latch's one-shot semantics + context→session resolution without standing up a
full Controller (the heavy ``__init__`` is bypassed via ``object.__new__``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.controller import Controller


def _bare_controller() -> Controller:
    controller = object.__new__(Controller)
    controller._sessions_turn_failed = set()
    return controller


def _ctx(session_id):
    return SimpleNamespace(platform_specific={"agent_session_id": session_id})


def test_session_id_from_context_reads_agent_session_id():
    assert Controller._session_id_from_context(_ctx("ses-1")) == "ses-1"
    # IM / CLI turns carry no workbench session id → resolve to None (no latch).
    assert Controller._session_id_from_context(SimpleNamespace(platform_specific={})) is None
    assert Controller._session_id_from_context(SimpleNamespace(platform_specific=None)) is None
    assert Controller._session_id_from_context(None) is None


def test_note_and_pop_turn_failed_is_one_shot():
    controller = _bare_controller()
    controller.note_turn_failed(_ctx("ses-1"))
    # A context with no session id latches nothing (off-workbench).
    controller.note_turn_failed(SimpleNamespace(platform_specific={}))

    assert controller.pop_turn_failed("ses-1") is True
    # Consumed: a second pop is False so the next turn starts clean.
    assert controller.pop_turn_failed("ses-1") is False
    assert controller.pop_turn_failed("ses-unknown") is False


def test_mark_turn_running_clears_stale_failed_latch(monkeypatch):
    controller = _bare_controller()
    # A prior (e.g. harness) turn failed but no _run_turn consumed the latch.
    controller._sessions_turn_failed.add("ses-1")
    calls = []
    monkeypatch.setattr(controller, "set_agent_status", lambda sid, status: calls.append((sid, status)))

    controller.mark_turn_running("ses-1")

    # Start clears the stale latch so this turn isn't mis-classified as failed.
    assert controller.pop_turn_failed("ses-1") is False
    assert calls == [("ses-1", "running")]


def test_status_helpers_are_noops_without_session_id(monkeypatch):
    controller = _bare_controller()
    calls = []
    monkeypatch.setattr(controller, "set_agent_status", lambda sid, status: calls.append((sid, status)))
    controller.mark_turn_running(None)
    assert calls == []
    assert controller.pop_turn_failed(None) is False
