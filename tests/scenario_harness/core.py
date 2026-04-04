import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

from modules.im import MessageContext


class ScenarioEventProbe:
    """Collect user-visible events emitted by a scenario run."""

    def __init__(self):
        self.events = []

    def record(self, kind: str, *payload):
        self.events.append((kind, *payload))

    def rendered_texts(self) -> list[str]:
        return [event[1] for event in self.events if len(event) > 1 and isinstance(event[1], str)]

    def matching(self, kind: str):
        return [event for event in self.events if event[0] == kind]


class ScenarioIMClient:
    """Capture outbound IM traffic while keeping the surface close to real adapters."""

    def __init__(self, probe: ScenarioEventProbe | None = None):
        self.probe = probe or ScenarioEventProbe()

    async def send_message(self, context, text, parse_mode=None):
        self.probe.record("message", text)
        return f"msg-{len(self.probe.events)}"

    async def send_message_with_buttons(self, context, text, keyboard, parse_mode=None):
        self.probe.record("buttons", text, keyboard)
        return f"btn-{len(self.probe.events)}"

    def should_use_thread_for_reply(self):
        return False

    def rendered_texts(self) -> list[str]:
        return self.probe.rendered_texts()


class ScenarioControllerBase:
    """Small reusable controller surface for service-boundary scenario tests."""

    def __init__(self, *, default_backend: str = "codex", language: str = "en", platform: str = "slack"):
        self._default_backend = default_backend
        self.config = SimpleNamespace(
            platform=platform,
            language=language,
            agents=SimpleNamespace(
                codex=SimpleNamespace(cli_path="codex"),
                claude=SimpleNamespace(cli_path="claude"),
                opencode=SimpleNamespace(cli_path="opencode"),
            ),
        )
        self.im_probe = ScenarioEventProbe()
        self.im_client = ScenarioIMClient(self.im_probe)
        self.agent_service = SimpleNamespace(agents={})
        self.sessions = SimpleNamespace(get_agent_session_id=lambda *args, **kwargs: None)
        self.session_handler = SimpleNamespace(
            get_session_info=lambda context: ("base-1", "/tmp/workdir", "base-1:/tmp/workdir"),
            get_working_path=lambda context: "/tmp/workdir",
        )

    def get_im_client_for_context(self, context):
        return self.im_client

    def _get_settings_key(self, context):
        return context.channel_id

    def _get_lang(self):
        return self.config.language

    def resolve_agent_for_context(self, context):
        return self._default_backend

    def get_opencode_overrides(self, context):
        return (None, None, None)


class FakeProcess:
    """Simple completion-controlled process stub for scenario flows."""

    def __init__(self):
        self.returncode = None
        self.stdout = SimpleNamespace(readline=AsyncMock(return_value=b""))
        self._done = asyncio.Event()
        self.terminate_calls = 0
        self.kill_calls = 0

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def finish(self, returncode=0):
        self.returncode = returncode
        self._done.set()

    def terminate(self):
        self.terminate_calls += 1
        self.finish(-15)

    def kill(self):
        self.kill_calls += 1
        self.finish(-9)


class BaseScenarioHarness:
    """Minimal base harness shared by capability-specific scenario harnesses."""

    def __init__(self, controller: ScenarioControllerBase | None = None, *, user_id: str = "U1", channel_id: str = "C1"):
        self.controller = controller or ScenarioControllerBase()
        self.context = MessageContext(user_id=user_id, channel_id=channel_id)

    def make_context(self, *, user_id: str | None = None, channel_id: str | None = None) -> MessageContext:
        return MessageContext(
            user_id=user_id or self.context.user_id,
            channel_id=channel_id or self.context.channel_id,
        )

    @property
    def events(self):
        return self.controller.im_probe.events

    def rendered_texts(self) -> list[str]:
        return self.controller.im_probe.rendered_texts()


@dataclass
class ScenarioStep:
    """One named scenario action executed against a harness."""

    name: str
    action: object


class ScenarioRunner:
    """Run a sequence of named async steps against one harness instance."""

    def __init__(self, harness: BaseScenarioHarness):
        self.harness = harness
        self.history: list[str] = []

    async def run(self, *steps: ScenarioStep):
        for step in steps:
            self.history.append(step.name)
            result = step.action(self.harness)
            if asyncio.iscoroutine(result):
                await result


class ScenarioExpect:
    """Common assertions for scenario tests."""

    @staticmethod
    def text_contains(
        harness: BaseScenarioHarness,
        needle: str,
        *,
        index: int | None = None,
        case_sensitive: bool = False,
    ):
        texts = harness.rendered_texts()
        haystack = texts[index] if index is not None else "\n".join(texts)
        expected = needle
        if not case_sensitive:
            haystack = haystack.lower()
            expected = needle.lower()
        assert expected in haystack, f"Expected to find {needle!r} in scenario output, got: {haystack!r}"

    @staticmethod
    def flow_missing(harness, flow_key: str):
        assert flow_key not in harness.service._flows, f"Expected flow {flow_key} to be removed"

    @staticmethod
    def step_history(runner: ScenarioRunner, expected: list[str]):
        assert runner.history == expected, f"Expected step history {expected!r}, got {runner.history!r}"

    @staticmethod
    def button_callback_contains(harness: BaseScenarioHarness, needle: str):
        buttons = harness.controller.im_probe.matching("buttons")
        assert buttons, "Expected at least one button event"
        callbacks = []
        for _, _, keyboard in buttons:
            for row in getattr(keyboard, "buttons", []):
                for button in row:
                    callbacks.append(getattr(button, "callback_data", ""))
        assert any(needle in callback for callback in callbacks), (
            f"Expected one button callback to contain {needle!r}, got {callbacks!r}"
        )
