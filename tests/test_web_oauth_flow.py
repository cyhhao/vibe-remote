"""Regression tests for the Settings → Backends web OAuth flow.

Pins the public surface added in PR #282 R5 so the Claude/Codex Settings
page can drive OAuth from the browser instead of asking users to copy a
``claude login`` command into a terminal. The state machine itself
(``WebAuthFlow``) and the four web methods on ``AgentAuthService`` are
exercised without spawning real subprocesses by injecting stub flows
into ``_web_flows`` and mocking ``_send_claude_callback``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from core.agent_auth_service import AgentAuthService, WebAuthFlow


class _Backend:
    cli_path = "/usr/bin/echo"  # any binary that exists is fine


class _Agents:
    claude = _Backend
    codex = _Backend
    opencode = _Backend


class _Config:
    agents = _Agents()
    language = "en"


class _StubController:
    """Minimal controller stand-in (see ``vibe/api.py::_WebControllerStub``)."""

    agent_service = None
    session_handler = None
    im_client = None
    config = _Config()


@pytest.fixture
def service() -> AgentAuthService:
    return AgentAuthService(_StubController())


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_unsupported_backend_raises(service: AgentAuthService) -> None:
    with pytest.raises(ValueError, match="unsupported_backend"):
        _run(service.start_web_setup("opencode"))


def test_status_for_unknown_flow_returns_flow_not_found(service: AgentAuthService) -> None:
    result = service.get_web_flow_status("nonexistent")
    assert result == {"ok": False, "error": "flow_not_found"}


def test_submit_code_unknown_flow(service: AgentAuthService) -> None:
    result = _run(service.submit_web_code("nonexistent", "abc#def"))
    assert result == {"ok": False, "error": "flow_not_found"}


def test_submit_code_rejected_for_codex(service: AgentAuthService) -> None:
    # Codex device-auth never asks for a code; submitting one is a UI bug.
    flow = WebAuthFlow(flow_id="cdx1", backend="codex", state="awaiting_code", awaiting_code=True)
    service._web_flows[flow.flow_id] = flow
    result = _run(service.submit_web_code("cdx1", "abc#def"))
    assert result == {"ok": False, "error": "code_not_supported"}


def test_submit_code_rejected_when_not_awaiting(service: AgentAuthService) -> None:
    flow = WebAuthFlow(flow_id="cl1", backend="claude", state="verifying", awaiting_code=False)
    service._web_flows[flow.flow_id] = flow
    result = _run(service.submit_web_code("cl1", "abc#def"))
    assert result == {"ok": False, "error": "not_awaiting_code"}


def test_submit_code_invalid_format(service: AgentAuthService) -> None:
    flow = WebAuthFlow(
        flow_id="cl2",
        backend="claude",
        state="awaiting_code",
        awaiting_code=True,
        claude_client=object(),  # presence-check only
    )
    service._web_flows[flow.flow_id] = flow

    # Missing separator.
    assert _run(service.submit_web_code("cl2", "no-hash-here")) == {
        "ok": False,
        "error": "invalid_format",
    }
    # Empty left half.
    assert _run(service.submit_web_code("cl2", "#statehere")) == {
        "ok": False,
        "error": "invalid_format",
    }
    # Empty right half.
    assert _run(service.submit_web_code("cl2", "code#")) == {
        "ok": False,
        "error": "invalid_format",
    }


def test_submit_code_happy_path_transitions_to_verifying(
    service: AgentAuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_send = AsyncMock()
    monkeypatch.setattr(service, "_send_claude_callback", mock_send)

    fake_client = object()
    flow = WebAuthFlow(
        flow_id="cl3",
        backend="claude",
        state="awaiting_code",
        awaiting_code=True,
        claude_client=fake_client,
        url="https://claude.ai/oauth/authorize?...",
    )
    service._web_flows[flow.flow_id] = flow

    result = _run(service.submit_web_code("cl3", "  authcode  #  state-token  "))
    assert result == {"ok": True}
    mock_send.assert_awaited_once_with(fake_client, "authcode", "state-token")
    assert flow.state == "verifying"
    assert flow.awaiting_code is False


def test_status_returns_serializable_snapshot(service: AgentAuthService) -> None:
    flow = WebAuthFlow(
        flow_id="cdx2",
        backend="codex",
        state="awaiting_code",
        url="https://auth.openai.com/codex/device",
        device_code="ABCD-EFGH",
        awaiting_code=False,
    )
    service._web_flows[flow.flow_id] = flow
    result = service.get_web_flow_status("cdx2")
    assert result == {
        "ok": True,
        "flow_id": "cdx2",
        "backend": "codex",
        "state": "awaiting_code",
        "url": "https://auth.openai.com/codex/device",
        "device_code": "ABCD-EFGH",
        "awaiting_code": False,
        "error": None,
    }


def test_cancel_unknown_flow(service: AgentAuthService) -> None:
    result = _run(service.cancel_web_flow("nope"))
    assert result == {"ok": False, "error": "flow_not_found"}


def test_cancel_removes_flow_and_marks_state(service: AgentAuthService) -> None:
    flow = WebAuthFlow(flow_id="any", backend="codex", state="awaiting_code")
    service._web_flows[flow.flow_id] = flow
    result = _run(service.cancel_web_flow("any"))
    assert result == {"ok": True}
    assert "any" not in service._web_flows
    assert flow.state == "cancelled"


def test_post_web_success_hook_invocation_when_set(
    service: AgentAuthService,
) -> None:
    """Hook fires once after a successful flow; absence is a no-op."""
    calls: list[str] = []

    def hook(backend: str) -> None:
        calls.append(backend)

    service._post_web_success_hook = hook
    _run(service._invoke_post_web_success_hook("codex"))
    assert calls == ["codex"]


def test_post_web_success_hook_swallows_exceptions(service: AgentAuthService) -> None:
    """A misbehaving hook must not surface into the flow waiter."""

    def hook(_backend: str) -> None:
        raise RuntimeError("boom")

    service._post_web_success_hook = hook
    # Should NOT raise.
    _run(service._invoke_post_web_success_hook("claude"))


def test_post_web_success_hook_unset_is_safe(service: AgentAuthService) -> None:
    service._post_web_success_hook = None
    _run(service._invoke_post_web_success_hook("claude"))
