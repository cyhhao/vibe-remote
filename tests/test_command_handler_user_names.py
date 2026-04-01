import importlib.util
import sys
import types
import unittest
from pathlib import Path

from modules.im import MessageContext


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_command_handlers_class():
    agents_module = types.ModuleType("modules.agents")
    agents_module.__path__ = [str(ROOT / "modules" / "agents")]
    setattr(agents_module, "AgentRequest", type("AgentRequest", (), {}))
    setattr(
        agents_module, "get_agent_display_name", lambda agent_name, fallback=None: agent_name or fallback or "Unknown"
    )
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
        ("core.handlers.command_handlers", ROOT / "core" / "handlers" / "command_handlers.py"),
    ):
        spec = importlib.util.spec_from_file_location(module_name, relative_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

    return sys.modules["core.handlers.command_handlers"].CommandHandlers


CommandHandlers = _load_command_handlers_class()


class _StubIMClient:
    def __init__(self, user_info):
        self.user_info = user_info
        self.sent_messages = []
        self.sent_contexts = []
        self.sent_button_messages = []
        self.channel_info_calls = []
        self.formatter = None
        self.started_topic_context = None

    async def get_user_info(self, user_id):
        return self.user_info

    async def get_channel_info(self, channel_id):
        self.channel_info_calls.append(channel_id)
        return {"id": channel_id, "name": channel_id}

    async def send_message(self, context, text, parse_mode=None):
        self.sent_contexts.append(context)
        self.sent_messages.append((context.channel_id, text))
        return "T1"

    async def send_message_with_buttons(self, context, text, keyboard, parse_mode=None):
        self.sent_button_messages.append((context.channel_id, text, keyboard))
        return "T2"

    async def start_new_topic_session(self, context):
        return self.started_topic_context


class _StubSettingsManager:
    def __init__(self):
        self.bind_calls = []

    def is_bound_user(self, user_id):
        return False

    def bind_user_with_code(self, user_id, display_name, code, dm_chat_id=""):
        self.bind_calls.append((user_id, display_name, code, dm_chat_id))
        return True, False


class _StubController:
    def __init__(self, user_info):
        self.config = type("Config", (), {"platform": "slack", "language": "zh"})()
        self.im_client = _StubIMClient(user_info)
        self.settings_manager = _StubSettingsManager()
        self.sessions = self.settings_manager
        self.session_manager = object()
        self.receiver_tasks = {}
        self.agent_service = type("AgentService", (), {"default_agent": "codex"})()

    def _get_settings_key(self, context: MessageContext) -> str:
        return context.user_id if context.channel_id.startswith("D") else context.channel_id

    def _get_session_key(self, context: MessageContext) -> str:
        return f"{getattr(context, 'platform', None) or 'test'}::{self._get_settings_key(context)}"

    def resolve_agent_for_context(self, context: MessageContext) -> str:
        return "codex"


class CommandHandlerUserNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_bind_success_prefers_real_name_when_display_name_blank(self):
        controller = _StubController(
            {
                "display_name": "",
                "display_name_normalized": "",
                "real_name": "Alex",
                "real_name_normalized": "Alex",
                "name": "cyh",
            }
        )
        handler = CommandHandlers(controller)
        context = MessageContext(user_id="U0E0FM3QT", channel_id="D123")

        await handler.handle_bind(context, "bind-code")

        self.assertEqual(
            controller.settings_manager.bind_calls,
            [("U0E0FM3QT", "Alex", "bind-code", "D123")],
        )
        self.assertEqual(
            controller.im_client.sent_messages,
            [("D123", "✅ 绑定成功！欢迎，Alex。你现在可以通过私信使用 Vibe Remote。")],
        )

    async def test_wechat_start_message_uses_localized_compact_commands(self):
        controller = _StubController({"display_name": "小王"})
        setattr(controller.config, "platform", "wechat")
        handler = CommandHandlers(controller)
        context = MessageContext(user_id="wx-user", channel_id="wx-chat")

        await handler.handle_start(context)

        self.assertEqual(len(controller.im_client.sent_messages), 1)
        _, message = controller.im_client.sent_messages[0]
        self.assertIn("欢迎使用 Vibe Remote！", message)
        self.assertIn("你好 小王！", message)
        self.assertIn("/start - 显示欢迎消息", message)
        self.assertIn("/setcwd <路径> - 设置工作目录", message)
        self.assertIn("/resume - 恢复当前目录下最近的会话", message)
        self.assertIn("/new - 开启一个全新的会话", message)
        self.assertNotIn("User ID", message)
        self.assertNotIn("How it works", message)
        self.assertNotIn("频道：", message)

    async def test_new_command_sends_fresh_session_confirmation(self):
        controller = _StubController({"display_name": "小王"})
        controller.agent_service.clear_sessions = _clear_sessions  # type: ignore[attr-defined]
        handler = CommandHandlers(controller)
        context = MessageContext(user_id="wx-user", channel_id="wx-chat")

        await handler.handle_new(context)

        self.assertEqual(
            controller.im_client.sent_messages,
            [("wx-chat", "🆕 已开启新的会话。你下一条消息会从全新对话开始。")],
        )

    async def test_telegram_new_command_creates_topic_session_when_supported(self):
        controller = _StubController({"display_name": "Alex"})
        setattr(controller.config, "platform", "telegram")
        controller.agent_service.clear_sessions = _clear_sessions  # type: ignore[attr-defined]
        handler = CommandHandlers(controller)
        controller.im_client.started_topic_context = MessageContext(
            user_id="42",
            channel_id="-100123",
            thread_id="99",
            platform="telegram",
        )
        context = MessageContext(
            user_id="42",
            channel_id="-100123",
            thread_id="1",
            platform="telegram",
            platform_specific={"platform": "telegram"},
        )

        await handler.handle_new(context)

        self.assertEqual(
            controller.im_client.sent_messages,
            [("-100123", "🆕 已开启新的会话。你下一条消息会从全新对话开始。")],
        )
        self.assertEqual(controller.im_client.sent_contexts[0].thread_id, "99")

    async def test_slack_dm_start_skips_channel_info_lookup(self):
        controller = _StubController({"display_name": "Alex"})
        handler = CommandHandlers(controller)
        context = MessageContext(
            user_id="U0E0FM3QT",
            channel_id="D123",
            platform="slack",
            platform_specific={"is_dm": True, "platform": "slack"},
        )

        await handler.handle_start(context)

        self.assertEqual(controller.im_client.channel_info_calls, [])
        self.assertEqual(len(controller.im_client.sent_button_messages), 1)
        _, text, _ = controller.im_client.sent_button_messages[0]
        self.assertIn("私信", text)


async def _clear_sessions(_settings_key):
    return {}


if __name__ == "__main__":
    unittest.main()
