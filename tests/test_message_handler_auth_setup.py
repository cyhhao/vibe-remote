import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.im import MessageContext


def _load_message_handler_class():
    agents_module = types.ModuleType("modules.agents")
    agents_module.__path__ = [str(ROOT / "modules" / "agents")]
    setattr(agents_module, "AgentRequest", type("AgentRequest", (), {}))
    sys.modules["modules.agents"] = agents_module
    agents_base_module = types.ModuleType("modules.agents.base")
    setattr(agents_base_module, "AgentRequest", type("AgentRequest", (), {}))
    sys.modules["modules.agents.base"] = agents_base_module

    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = [str(ROOT / "core")]
    sys.modules["core"] = core_pkg

    handlers_pkg = types.ModuleType("core.handlers")
    handlers_pkg.__path__ = [str(ROOT / "core" / "handlers")]
    sys.modules["core.handlers"] = handlers_pkg

    for module_name, relative_path in (
        ("core.handlers.base", ROOT / "core" / "handlers" / "base.py"),
        ("core.handlers.message_handler", ROOT / "core" / "handlers" / "message_handler.py"),
    ):
        spec = importlib.util.spec_from_file_location(module_name, relative_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

    return sys.modules["core.handlers.message_handler"].MessageHandler


MessageHandler = _load_message_handler_class()


class _StubSessions:
    def is_message_already_processed(self, channel_id, thread_ts, message_ts):
        return False

    def record_processed_message(self, channel_id, thread_ts, message_ts):
        return None


class _StubSettingsManager:
    def __init__(self):
        self.sessions = _StubSessions()

    def get_channel_routing(self, settings_key):
        return None

    def is_message_type_hidden(self, settings_key, canonical_type):
        return False


class _StubIMClient:
    def __init__(self):
        self.sent_messages = []
        self.formatter = types.SimpleNamespace(format_error=lambda text: text, format_warning=lambda text: text)

    async def send_message(self, context, text, parse_mode=None, reply_to=None):
        self.sent_messages.append((context.channel_id, text))
        return "msg-1"


class _StubController:
    def __init__(self):
        self.config = types.SimpleNamespace(platform="slack", ack_mode="reaction", include_user_info=False, language="en")
        self.im_client = _StubIMClient()
        self.settings_manager = _StubSettingsManager()
        self.session_manager = object()
        self.session_handler = None
        self.receiver_tasks = {}
        self.agent_service = types.SimpleNamespace(default_agent="codex")
        self.settings_handler = types.SimpleNamespace()
        self.command_handler = types.SimpleNamespace()
        self.agent_auth_service = types.SimpleNamespace(handle_setup_callback=AsyncMock())

    def get_im_client_for_context(self, context):
        return self.im_client

    def _get_settings_key(self, context):
        return context.channel_id

    def _get_session_key(self, context):
        return f"slack::{context.channel_id}"

    def _get_lang(self):
        return "en"


class MessageHandlerAuthSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_setup_callback_routes_to_agent_auth_service(self):
        controller = _StubController()
        controller.settings_handler.handle_info_message_types = AsyncMock()
        controller.settings_handler.handle_info_how_it_works = AsyncMock()
        controller.command_handler.handle_cwd = AsyncMock()
        controller.command_handler.handle_change_cwd_modal = AsyncMock()
        controller.command_handler.handle_new = AsyncMock()
        controller.command_handler.handle_resume = AsyncMock()
        controller.settings_handler.handle_settings = AsyncMock()
        controller.settings_handler.handle_routing = AsyncMock()

        handler = MessageHandler(controller)
        context = MessageContext(user_id="U1", channel_id="C1", platform="slack")

        await handler.handle_callback_query(context, "auth_setup:auto")

        controller.agent_auth_service.handle_setup_callback.assert_awaited_once_with(context, "auth_setup:auto")


if __name__ == "__main__":
    unittest.main()
