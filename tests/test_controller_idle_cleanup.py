from types import SimpleNamespace

from config.v2_config import DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS
from core.controller import Controller


def test_idle_cleanup_timeouts_disable_codex_when_backend_config_absent() -> None:
    controller = object.__new__(Controller)
    controller.config = SimpleNamespace(
        claude=SimpleNamespace(idle_timeout_seconds=0),
        codex=None,
    )

    claude_timeout, codex_timeout = Controller._get_idle_cleanup_timeouts(controller)

    assert claude_timeout == 0
    assert codex_timeout == 0


def test_idle_cleanup_timeouts_preserve_explicit_codex_timeout() -> None:
    controller = object.__new__(Controller)
    controller.config = SimpleNamespace(
        claude=SimpleNamespace(idle_timeout_seconds=300),
        codex=SimpleNamespace(idle_timeout_seconds=900),
    )

    claude_timeout, codex_timeout = Controller._get_idle_cleanup_timeouts(controller)

    assert claude_timeout == 300
    assert codex_timeout == 900


def test_idle_cleanup_timeouts_fall_back_to_shared_default_when_backend_config_omits_value() -> None:
    controller = object.__new__(Controller)
    controller.config = SimpleNamespace(
        claude=SimpleNamespace(),
        codex=SimpleNamespace(),
    )

    claude_timeout, codex_timeout = Controller._get_idle_cleanup_timeouts(controller)

    assert claude_timeout == DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS
    assert codex_timeout == DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS
