import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.im import MessageContext


def _load_message_handler_class():
    with patch.dict(sys.modules, {}, clear=False):
        agents_module = types.ModuleType("modules.agents")
        setattr(agents_module, "AgentRequest", type("AgentRequest", (), {}))
        sys.modules["modules.agents"] = agents_module

        core_pkg = types.ModuleType("core")
        core_pkg.__path__ = [str(ROOT / "core")]
        sys.modules["core"] = core_pkg

        handlers_pkg = types.ModuleType("core.handlers")
        handlers_pkg.__path__ = [str(ROOT / "core" / "handlers")]
        sys.modules["core.handlers"] = handlers_pkg

        base_name = "core.handlers.base"
        base_spec = importlib.util.spec_from_file_location(base_name, ROOT / "core" / "handlers" / "base.py")
        assert base_spec is not None
        assert base_spec.loader is not None
        base_module = importlib.util.module_from_spec(base_spec)
        sys.modules[base_name] = base_module
        base_spec.loader.exec_module(base_module)

        module_name = "core.handlers.message_handler"
        spec = importlib.util.spec_from_file_location(module_name, ROOT / "core" / "handlers" / "message_handler.py")
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        return module.MessageHandler


MessageHandler = _load_message_handler_class()


class _StubIMClient:
    def __init__(self, user_info):
        self.user_info = user_info
        self.formatter = None

    async def get_user_info(self, user_id):
        return self.user_info


class _StubController:
    def __init__(self, user_info):
        self.config = type("Config", (), {"platform": "slack"})()
        self.im_client = _StubIMClient(user_info)
        self.settings_manager = type("Settings", (), {})()
        self.session_manager = object()
        self.receiver_tasks = {}


class MessageHandlerUserInfoTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepend_user_info_prefers_real_name_over_name(self):
        handler = MessageHandler(_StubController({"display_name": "", "real_name": "Alex", "name": "cyh"}))
        context = MessageContext(user_id="U0E0FM3QT", channel_id="C1")

        result = await handler._prepend_user_info(context, "hello")

        self.assertEqual(result, "[Alex<U0E0FM3QT>] hello")

    async def test_prepend_user_info_uses_display_name_when_present(self):
        handler = MessageHandler(_StubController({"display_name": "Alex Chen", "real_name": "Alex", "name": "cyh"}))
        context = MessageContext(user_id="U0E0FM3QT", channel_id="C1")

        result = await handler._prepend_user_info(context, "hello")

        self.assertEqual(result, "[Alex Chen<U0E0FM3QT>] hello")


if __name__ == "__main__":
    unittest.main()
