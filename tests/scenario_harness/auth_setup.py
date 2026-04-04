import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.agent_auth_service import AgentAuthService
from modules.im import MessageContext


class ScenarioIMClient:
    """Capture user-visible outputs emitted during a scenario run."""

    def __init__(self):
        self.events = []

    async def send_message(self, context, text, parse_mode=None):
        self.events.append(("message", text))
        return f"msg-{len(self.events)}"

    async def send_message_with_buttons(self, context, text, keyboard, parse_mode=None):
        self.events.append(("buttons", text, keyboard))
        return f"btn-{len(self.events)}"

    def rendered_texts(self):
        return [event[1] for event in self.events]


class ScenarioController:
    """Minimal controller surface for AgentAuthService scenario tests."""

    def __init__(self):
        self.config = SimpleNamespace(
            platform="slack",
            language="en",
            agents=SimpleNamespace(
                codex=SimpleNamespace(cli_path="codex"),
                claude=SimpleNamespace(cli_path="claude"),
                opencode=SimpleNamespace(cli_path="opencode"),
            ),
        )
        self.im_client = ScenarioIMClient()
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
        return "en"

    def resolve_agent_for_context(self, context):
        return "codex"

    def get_opencode_overrides(self, context):
        return (None, None, None)


class FakeProcess:
    """Simple completion-controlled process stub for setup flows."""

    def __init__(self):
        self.returncode = None
        self.stdout = SimpleNamespace(readline=AsyncMock(return_value=b""))
        self._done = asyncio.Event()

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def finish(self, returncode=0):
        self.returncode = returncode
        self._done.set()

    def terminate(self):
        self.finish(-15)

    def kill(self):
        self.finish(-9)


class AuthSetupScenarioHarness:
    """Reusable harness for capability-level auth/setup scenarios."""

    def __init__(self):
        self.controller = ScenarioController()
        self.service = AgentAuthService(self.controller)
        self.context = MessageContext(user_id="U1", channel_id="C1")

    def flow(self, backend: str):
        return self.service._flows[f"C1:{backend}"]

    def rendered_texts(self):
        return self.controller.im_client.rendered_texts()
