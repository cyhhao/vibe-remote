import asyncio
import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.im import MessageContext


def _load_message_handler_class():
    with patch.dict(sys.modules, {}, clear=False):
        agents_module = types.ModuleType("modules.agents")
        agents_module.__path__ = [str(ROOT / "modules" / "agents")]

        @dataclass
        class _AgentRequest:
            context: MessageContext
            message: str
            working_path: str
            base_session_id: str
            composite_session_id: str
            session_key: str
            ack_message_id: str | None = None
            subagent_name: str | None = None
            subagent_key: str | None = None
            subagent_model: str | None = None
            subagent_reasoning_effort: str | None = None
            ack_reaction_message_id: str | None = None
            ack_reaction_emoji: str | None = None
            typing_indicator_active: bool = False
            typing_indicator_task: asyncio.Task | None = None
            files: list | None = None

        setattr(agents_module, "AgentRequest", _AgentRequest)
        sys.modules["modules.agents"] = agents_module
        agents_base_module = types.ModuleType("modules.agents.base")
        setattr(agents_base_module, "AgentRequest", _AgentRequest)
        sys.modules["modules.agents.base"] = agents_base_module

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


class _StubIMClient:
    def __init__(self, *, typing_result: bool):
        self.typing_result = typing_result
        self.typing_calls = []
        self.clear_calls = []
        self.reactions = []
        self.sent_messages = []
        self.removed_keyboards = []
        self.formatter = type("Formatter", (), {"format_error": staticmethod(lambda text: text)})()

    def should_use_thread_for_reply(self):
        return False

    async def prepare_turn_context(self, context, source):
        return context

    async def get_user_info(self, user_id):
        return {"display_name": f"user:{user_id}"}

    async def download_file_to_path(self, file_info, target_path):
        self.sent_messages.append(("download", file_info["name"], target_path))
        from modules.im.base import FileDownloadResult

        return FileDownloadResult(False, "not implemented")

    async def send_typing_indicator(self, context):
        self.typing_calls.append((context.channel_id, context.user_id))
        return self.typing_result

    async def clear_typing_indicator(self, context):
        self.clear_calls.append((context.channel_id, context.user_id))
        return True

    async def add_reaction(self, context, message_id, emoji):
        self.reactions.append((context.channel_id, message_id, emoji))
        return True

    async def remove_reaction(self, context, message_id, emoji):
        self.reactions.append((context.channel_id, message_id, f"remove:{emoji}"))
        return True

    async def send_message(self, context, text, parse_mode=None, reply_to=None):
        self.sent_messages.append((context.channel_id, text))
        return "msg-1"

    async def remove_inline_keyboard(self, context, message_id, text=None, parse_mode=None):
        self.removed_keyboards.append((context.channel_id, context.platform, message_id))
        return True


class _StubAgentService:
    def __init__(self):
        self.default_agent = "codex"
        self.requests = []

    async def handle_message(self, agent_name, request):
        self.requests.append((agent_name, request))


class _StubController:
    def __init__(self, *, platform: str, ack_mode: str, typing_result: bool):
        self.config = type(
            "Config",
            (),
            {"platform": platform, "ack_mode": ack_mode, "include_user_info": False, "language": "en"},
        )()
        self.im_client = _StubIMClient(typing_result=typing_result)
        self.settings_manager = _StubSettingsManager()
        self.session_manager = object()
        self.session_handler = None
        self.receiver_tasks = {}
        self.agent_service = _StubAgentService()
        self.settings_handler = type("Settings", (), {})()
        self.command_handler = type("Cmd", (), {"handle_start": staticmethod(lambda context, args: None)})()

    def update_thread_message_id(self, context):
        return None

    def get_im_client_for_context(self, context):
        return self.im_client

    def resolve_agent_for_context(self, context):
        return "codex"

    def _get_settings_key(self, context):
        return context.channel_id

    def _get_session_key(self, context):
        return f"{getattr(context, 'platform', None) or 'test'}::{self._get_settings_key(context)}"

    def _get_lang(self):
        return "en"


class _StubSessionHandler:
    def __init__(self):
        self.alias_calls = []

    @staticmethod
    def get_session_info(context, source="human"):
        return ("base-session", "/tmp", "base-session:/tmp")

    @staticmethod
    def should_allocate_scheduled_anchor(context, source="human"):
        return False

    def alias_session_base(self, context, *, source_base_session_id, alias_base_session_id, clear_source=False):
        self.alias_calls.append(
            {
                "source_base_session_id": source_base_session_id,
                "alias_base_session_id": alias_base_session_id,
                "clear_source": clear_source,
            }
        )
        return False


class MessageHandlerTypingTests(unittest.IsolatedAsyncioTestCase):
    async def test_wechat_forces_typing_even_when_ack_mode_is_message(self):
        controller = _StubController(platform="wechat", ack_mode="message", typing_result=True)
        handler = MessageHandler(controller)
        handler.set_session_handler(_StubSessionHandler())
        context = MessageContext(user_id="U1", channel_id="C1", message_id="m1")

        await handler.handle_user_message(context, "hello")

        _, request = controller.agent_service.requests[0]
        self.assertIsNone(request.ack_message_id)
        self.assertTrue(request.typing_indicator_active)
        self.assertEqual(controller.im_client.sent_messages, [])
        self.assertEqual(controller.im_client.reactions, [])
        self.assertGreaterEqual(len(controller.im_client.typing_calls), 1)

        await handler._remove_ack_reaction(context, request)
        self.assertEqual(controller.im_client.clear_calls, [("C1", "U1")])

    async def test_typing_mode_falls_back_to_reaction_when_platform_lacks_typing(self):
        controller = _StubController(platform="slack", ack_mode="typing", typing_result=False)
        handler = MessageHandler(controller)
        handler.set_session_handler(_StubSessionHandler())
        context = MessageContext(user_id="U1", channel_id="C1", message_id="m1")

        await handler.handle_user_message(context, "hello")

        _, request = controller.agent_service.requests[0]
        self.assertFalse(request.typing_indicator_active)
        self.assertEqual(request.ack_reaction_message_id, "m1")
        self.assertEqual(request.ack_reaction_emoji, ":eyes:")
        self.assertEqual(controller.im_client.reactions, [("C1", "m1", ":eyes:")])

    async def test_reply_anchor_alias_keeps_original_anchor_mapping(self):
        controller = _StubController(platform="discord", ack_mode="reaction", typing_result=True)
        handler = MessageHandler(controller)
        session_handler = _StubSessionHandler()
        handler.set_session_handler(session_handler)
        context = MessageContext(
            user_id="U1",
            channel_id="C1",
            thread_id="thread-1",
            message_id="m1",
            platform="discord",
            platform_specific={"reply_anchor_base_session_id": "discord_anchor-1"},
        )

        await handler.handle_user_message(context, "hello")

        self.assertEqual(
            session_handler.alias_calls,
            [
                {
                    "source_base_session_id": "discord_anchor-1",
                    "alias_base_session_id": "base-session",
                    "clear_source": False,
                }
            ],
        )

    async def test_wechat_context_forces_typing_even_when_primary_platform_is_slack(self):
        controller = _StubController(platform="slack", ack_mode="reaction", typing_result=True)
        handler = MessageHandler(controller)
        handler.set_session_handler(_StubSessionHandler())
        context = MessageContext(
            user_id="wx-user",
            channel_id="wx-chat",
            message_id="m1",
            platform="wechat",
            platform_specific={"platform": "wechat"},
        )

        await handler.handle_user_message(context, "hello")

        _, request = controller.agent_service.requests[0]
        self.assertTrue(request.typing_indicator_active)
        self.assertEqual(controller.im_client.reactions, [])
        self.assertGreaterEqual(len(controller.im_client.typing_calls), 1)

    async def test_platform_specific_client_is_used_for_user_info(self):
        controller = _StubController(platform="slack", ack_mode="reaction", typing_result=True)
        handler = MessageHandler(controller)
        context = MessageContext(user_id="wx-user", channel_id="wx-chat", platform="wechat")

        class _WechatClient(_StubIMClient):
            async def get_user_info(self, user_id):
                return {"display_name": "WeChat User"}

        wechat_client = _WechatClient(typing_result=True)
        controller.get_im_client_for_context = lambda _context: wechat_client  # type: ignore[method-assign]

        result = await handler._prepend_user_info(context, "hello")

        self.assertEqual(result, "[WeChat User<wx-user>] hello")

    async def test_resume_session_callback_preserves_platform(self):
        controller = _StubController(platform="slack", ack_mode="reaction", typing_result=True)
        setattr(
            controller,
            "session_handler",
            type("SessionHandler", (), {"handle_resume_session_submission": AsyncMock()})(),
        )
        handler = MessageHandler(controller)
        context = MessageContext(
            user_id="u1",
            channel_id="c1",
            thread_id="t1",
            platform="lark",
            platform_specific={"platform": "lark", "is_dm": False},
        )

        await handler.handle_callback_query(context, "resume_session:opencode:session-1")

        getattr(controller, "session_handler").handle_resume_session_submission.assert_awaited_once_with(
            user_id="u1",
            channel_id="c1",
            thread_id="t1",
            agent="opencode",
            session_id="session-1",
            is_dm=False,
            platform="lark",
        )

    async def test_quick_reply_callback_preserves_platform(self):
        controller = _StubController(platform="slack", ack_mode="reaction", typing_result=True)
        handler = MessageHandler(controller)
        handler.handle_user_message = AsyncMock()  # type: ignore[method-assign]
        context = MessageContext(
            user_id="u1",
            channel_id="chat1",
            message_id="om_123",
            platform="lark",
            platform_specific={"platform": "lark", "is_dm": False},
        )

        await handler.handle_callback_query(context, "quick_reply:继续")

        self.assertEqual(controller.im_client.removed_keyboards, [("chat1", "lark", "om_123")])
        handler.handle_user_message.assert_awaited_once()
        forwarded_context, forwarded_text = handler.handle_user_message.await_args.args
        self.assertEqual(forwarded_text, "继续")
        self.assertEqual(forwarded_context.platform, "lark")


if __name__ == "__main__":
    unittest.main()
