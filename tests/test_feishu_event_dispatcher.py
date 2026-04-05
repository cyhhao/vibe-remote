import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config.v2_config import LarkConfig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _install_opencode_utils_module() -> None:
    if "aiohttp" not in sys.modules:
        sys.modules["aiohttp"] = types.ModuleType("aiohttp")

    if "modules.agents.opencode.utils" in sys.modules:
        return

    if "modules.agents" not in sys.modules:
        agents_mod = types.ModuleType("modules.agents")
        agents_mod.__path__ = [str(ROOT / "modules" / "agents")]
        sys.modules["modules.agents"] = agents_mod
    if "modules.agents.opencode" not in sys.modules:
        opencode_mod = types.ModuleType("modules.agents.opencode")
        opencode_mod.__path__ = [str(ROOT / "modules" / "agents" / "opencode")]
        sys.modules["modules.agents.opencode"] = opencode_mod

    spec = importlib.util.spec_from_file_location(
        "modules.agents.opencode.utils",
        ROOT / "modules" / "agents" / "opencode" / "utils.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["modules.agents.opencode.utils"] = module
    spec.loader.exec_module(module)


_install_opencode_utils_module()

from modules.im.feishu import FeishuBot


class _FakeEventBuilder:
    def __init__(self):
        self.callbacks = {}

    def register_p2_im_message_receive_v1(self, callback):
        self.callbacks["message"] = callback
        return self

    def register_p2_card_action_trigger(self, callback):
        self.callbacks["card"] = callback
        return self

    def register_p2_im_message_reaction_created_v1(self, callback):
        self.callbacks["reaction_created"] = callback
        return self

    def register_p2_im_message_reaction_deleted_v1(self, callback):
        self.callbacks["reaction_deleted"] = callback
        return self

    def build(self):
        return self


class FeishuEventDispatcherTests(unittest.TestCase):
    def _make_bot(self) -> FeishuBot:
        return FeishuBot(LarkConfig(app_id="app-id", app_secret="app-secret"))

    def test_build_event_handler_registers_reaction_processors(self):
        bot = self._make_bot()
        builder = _FakeEventBuilder()
        fake_lark = types.SimpleNamespace(
            EventDispatcherHandler=SimpleNamespace(builder=lambda *args: builder),
        )

        with patch.dict(sys.modules, {"lark_oapi": fake_lark}, clear=False):
            handler = bot._build_event_handler()

        self.assertIs(handler, builder)
        self.assertIn("reaction_created", builder.callbacks)
        self.assertIn("reaction_deleted", builder.callbacks)

    def test_reaction_processors_ignore_events_without_error(self):
        bot = self._make_bot()
        builder = _FakeEventBuilder()
        fake_lark = types.SimpleNamespace(
            EventDispatcherHandler=SimpleNamespace(builder=lambda *args: builder),
        )

        with patch.dict(sys.modules, {"lark_oapi": fake_lark}, clear=False):
            bot._build_event_handler()

        data = SimpleNamespace(header=SimpleNamespace(event_id="evt-1", event_type="im.message.reaction.created_v1"))

        with patch("modules.im.feishu.logger") as mock_logger:
            builder.callbacks["reaction_created"](data)
            builder.callbacks["reaction_deleted"](data)

        self.assertEqual(mock_logger.debug.call_count, 2)
        mock_logger.error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
