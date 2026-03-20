import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from config.v2_config import LarkConfig
from modules.im.base import MessageContext


def _install_opencode_utils_module() -> None:
    if "modules.agents.opencode.utils" in sys.modules:
        return

    repo_root = Path(__file__).resolve().parents[1]
    agents_mod = types.ModuleType("modules.agents")
    agents_mod.__path__ = [str(repo_root / "modules" / "agents")]
    opencode_mod = types.ModuleType("modules.agents.opencode")
    opencode_mod.__path__ = [str(repo_root / "modules" / "agents" / "opencode")]

    sys.modules["modules.agents"] = agents_mod
    sys.modules["modules.agents.opencode"] = opencode_mod

    spec = importlib.util.spec_from_file_location(
        "modules.agents.opencode.utils",
        repo_root / "modules" / "agents" / "opencode" / "utils.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["modules.agents.opencode.utils"] = module
    spec.loader.exec_module(module)


_install_opencode_utils_module()

from modules.im.feishu import FeishuBot


class FeishuRoutingCardTests(unittest.IsolatedAsyncioTestCase):
    def _make_bot(self) -> FeishuBot:
        return FeishuBot(LarkConfig(app_id="app-id", app_secret="app-secret"))

    @staticmethod
    def _find_select(card, field_name):
        form = card["body"]["elements"][0]
        for element in form["elements"]:
            if element.get("tag") == "select_static" and element.get("name") == field_name:
                return element
        raise AssertionError(f"select_static {field_name} not found")

    async def test_claude_model_change_refreshes_reasoning_options(self):
        bot = self._make_bot()
        bot._patch_card_message = AsyncMock()

        current_routing = SimpleNamespace(
            claude_agent="helper",
            claude_model="claude-sonnet-4-5",
            claude_reasoning_effort="high",
            opencode_agent=None,
            opencode_model=None,
            opencode_reasoning_effort=None,
            codex_model=None,
            codex_reasoning_effort=None,
        )
        bot._routing_cache["chat:user"] = {
            "current_routing": current_routing,
            "draft_routing": bot._routing_draft_from_current(current_routing),
            "_selected_backend": "claude",
            "claude_agents": ["helper", "reviewer"],
            "claude_models": ["claude-sonnet-4-5", "claude-opus-4-6"],
            "opencode_agents": [],
            "opencode_models": {},
            "opencode_default_config": {},
            "codex_models": [],
        }

        context = MessageContext(user_id="user", channel_id="chat", message_id="om_123")

        handled = await bot._handle_routing_select_change(
            context,
            {"tag": "select_static", "name": "claude_model", "option": {"value": "claude-opus-4-6"}},
        )

        self.assertTrue(handled)
        bot._patch_card_message.assert_awaited_once()
        _, card = bot._patch_card_message.await_args.args

        reasoning_select = self._find_select(card, "claude_reasoning")
        reasoning_values = [option["value"] for option in reasoning_select["options"]]
        self.assertIn("max", reasoning_values)
        self.assertEqual(reasoning_select.get("initial_option"), "__default__")

        agent_select = self._find_select(card, "claude_agent")
        self.assertEqual(agent_select.get("initial_option"), "helper")

        draft = bot._routing_cache["chat:user"]["draft_routing"]
        self.assertEqual(draft["claude_model"], "claude-opus-4-6")
        self.assertIsNone(draft["claude_reasoning_effort"])


if __name__ == "__main__":
    unittest.main()
