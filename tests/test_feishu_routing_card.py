import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from config.v2_config import LarkConfig
from core.auth import AuthResult
from modules.im.base import MessageContext


def _install_opencode_utils_module() -> None:
    if "aiohttp" not in sys.modules:
        sys.modules["aiohttp"] = types.ModuleType("aiohttp")

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
        first_call = bot._patch_card_message.await_args
        assert first_call is not None
        _, card = first_call.args

        reasoning_select = self._find_select(card, "claude_reasoning")
        reasoning_values = [option["value"] for option in reasoning_select["options"]]
        self.assertIn("max", reasoning_values)
        self.assertEqual(reasoning_select.get("initial_option"), "__default__")

        agent_select = self._find_select(card, "claude_agent")
        self.assertEqual(agent_select.get("initial_option"), "helper")

        draft = bot._routing_cache["chat:user"]["draft_routing"]
        self.assertEqual(draft["claude_model"], "claude-opus-4-6")
        self.assertIsNone(draft["claude_reasoning_effort"])

    async def test_select_changes_with_different_options_are_not_deduped(self):
        bot = self._make_bot()
        bot._patch_card_message = AsyncMock()
        bot.check_authorization = lambda **kwargs: AuthResult(allowed=True)

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
            "claude_models": ["claude-sonnet-4-5", "claude-sonnet-4-6", "claude-opus-4-6"],
            "opencode_agents": [],
            "opencode_models": {},
            "opencode_default_config": {},
            "codex_models": [],
        }

        first_event = {
            "operator": {"open_id": "user"},
            "context": {"open_message_id": "om_123", "open_chat_id": "chat"},
            "action": {
                "tag": "select_static",
                "name": "claude_model",
                "option": {"value": "claude-sonnet-4-6"},
            },
        }
        second_event = {
            "operator": {"open_id": "user"},
            "context": {"open_message_id": "om_123", "open_chat_id": "chat"},
            "action": {
                "tag": "select_static",
                "name": "claude_model",
                "option": {"value": "claude-opus-4-6"},
            },
        }

        await bot._async_handle_card_action(first_event)
        await bot._async_handle_card_action(second_event)

        self.assertEqual(bot._patch_card_message.await_count, 2)
        draft = bot._routing_cache["chat:user"]["draft_routing"]
        self.assertEqual(draft["claude_model"], "claude-opus-4-6")
        latest_call = bot._patch_card_message.await_args
        assert latest_call is not None
        _, latest_card = latest_call.args
        reasoning_select = self._find_select(latest_card, "claude_reasoning")
        reasoning_values = [option["value"] for option in reasoning_select["options"]]
        self.assertIn("max", reasoning_values)

    async def test_quick_reply_card_action_sets_lark_platform(self):
        bot = self._make_bot()
        bot.check_authorization = lambda **kwargs: AuthResult(allowed=True)
        bot.on_callback_query_callback = AsyncMock()

        event = {
            "operator": {"open_id": "user"},
            "context": {"open_message_id": "om_123", "open_chat_id": "chat"},
            "action": {
                "tag": "button",
                "value": {"key": "quick_reply:继续"},
            },
        }

        await bot._async_handle_card_action(event)

        bot.on_callback_query_callback.assert_awaited_once()
        callback_context, callback_data = bot.on_callback_query_callback.await_args.args
        self.assertEqual(callback_data, "quick_reply:继续")
        self.assertEqual(callback_context.platform, "lark")
        self.assertEqual(callback_context.platform_specific["platform"], "lark")

    async def test_remove_inline_keyboard_prefers_cached_text(self):
        bot = self._make_bot()
        bot.edit_message = AsyncMock(return_value=True)
        bot._fetch_message_card_content = AsyncMock(return_value=None)
        bot._message_text_cache["om_123"] = "Original body"

        ok = await bot.remove_inline_keyboard(
            MessageContext(user_id="user", channel_id="chat", platform="lark"),
            "om_123",
        )

        self.assertTrue(ok)
        bot.edit_message.assert_awaited_once()
        _, kwargs = bot.edit_message.await_args
        self.assertEqual(kwargs["text"], "Original body")
        self.assertIsNone(kwargs["keyboard"])
        bot._fetch_message_card_content.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
