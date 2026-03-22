import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest

from modules.im import MessageContext
from vibe.i18n import t as i18n_t

REPO_ROOT = Path(__file__).resolve().parents[1]


def _ensure_package(name: str, path: Path) -> None:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module


def _load_module(name: str, relative_path: str):
    module = sys.modules.get(name)
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module {name} from {relative_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_ensure_package("modules", REPO_ROOT / "modules")
_ensure_package("modules.agents", REPO_ROOT / "modules/agents")
_ensure_package("modules.agents.opencode", REPO_ROOT / "modules/agents/opencode")
_ensure_package("modules.im", REPO_ROOT / "modules/im")

server_stub = types.ModuleType("modules.agents.opencode.server")
setattr(server_stub, "OpenCodeServerManager", type("OpenCodeServerManager", (), {}))
sys.modules.setdefault("modules.agents.opencode.server", server_stub)

base_module = _load_module("modules.agents.base", "modules/agents/base.py")
question_ui_module = _load_module("modules.agents.question_ui", "modules/agents/question_ui.py")
_load_module("modules.agents.opencode.types", "modules/agents/opencode/types.py")
question_handler_module = _load_module(
    "modules.agents.opencode.question_handler",
    "modules/agents/opencode/question_handler.py",
)

AgentRequest = base_module.AgentRequest
QuestionUIHandler = question_ui_module.QuestionUIHandler
PendingQuestion = question_ui_module.PendingQuestion
OpenCodeQuestionHandler = question_handler_module.OpenCodeQuestionHandler


class _StubIMClient:
    def __init__(self):
        self.sent_messages = []
        self.removed_keyboards = []
        self.reactions = []

    async def send_message(self, context, text, parse_mode=None, reply_to=None):
        message_id = f"M{len(self.sent_messages) + 1}"
        self.sent_messages.append((context.channel_id, context.thread_id, text, parse_mode, reply_to))
        return message_id

    async def remove_inline_keyboard(self, context, message_id, text=None, parse_mode=None):
        self.removed_keyboards.append((context.channel_id, context.thread_id, message_id, text, parse_mode))
        return True

    async def add_reaction(self, context, message_id, emoji):
        self.reactions.append((context.channel_id, context.thread_id, message_id, emoji))
        return True


class _StubConfig:
    def __init__(self, language="zh"):
        self.language = language


class _StubController:
    def __init__(self, lang="zh", platform="slack"):
        self.config = _StubConfig(language=lang)
        self.config.platform = platform
        self.emitted_messages = []
        self.cleared_consolidated_ids = []

    def _t(self, key, **kwargs):
        return i18n_t(key, self.config.language, **kwargs)

    async def emit_agent_message(self, context, message_type, text, parse_mode=None):
        self.emitted_messages.append((context.channel_id, message_type, text, parse_mode))

    async def clear_consolidated_message_id(self, context, trigger_message_id=None):
        self.cleared_consolidated_ids.append((context.channel_id, context.thread_id, trigger_message_id))


class _StubServer:
    def __init__(self, ok=True):
        self.ok = ok
        self.reply_calls = []
        self.abort_calls = []

    async def reply_question(self, question_id, directory, answers_payload):
        self.reply_calls.append((question_id, directory, answers_payload))
        return self.ok

    async def abort_session(self, session_id, directory):
        self.abort_calls.append((session_id, directory))
        return True


def _build_request(message: str) -> AgentRequest:
    return AgentRequest(
        context=MessageContext(
            user_id="U123",
            channel_id="C123",
            thread_id="T123",
            message_id="USER1",
        ),
        message=message,
        working_path="/tmp/work",
        base_session_id="base-session",
        composite_session_id="base-session:/tmp/work",
        settings_key="C123",
    )


class OpenCodeQuestionHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_question_answer_sends_visible_reply_echo(self):
        controller = _StubController(lang="zh")
        im_client = _StubIMClient()
        handler = OpenCodeQuestionHandler(controller, im_client, object())
        server = _StubServer(ok=True)
        request = _build_request("opencode_question:choose:2")
        handler._question_answer_events[request.base_session_id] = asyncio.Event()

        pending = {
            "session_id": "oc-session",
            "directory": "/tmp/work",
            "question_id": "q-1",
            "option_labels": ["Alpha", "Beta"],
            "prompt_message_id": "PROMPT1",
            "prompt_text": "Pick an option",
            "trigger_message_id": "TRIGGER1",
        }

        await handler.process_question_answer(request, pending, server)

        self.assertEqual(server.reply_calls, [("q-1", "/tmp/work", [["Beta"]])])
        self.assertEqual(
            im_client.removed_keyboards,
            [("C123", "T123", "PROMPT1", "Pick an option", "markdown")],
        )
        self.assertEqual(im_client.sent_messages[0][2], "回复：Beta")
        self.assertEqual(controller.cleared_consolidated_ids, [("C123", "T123", "TRIGGER1")])
        self.assertTrue(handler._question_answer_events[request.base_session_id].is_set())

    async def test_handle_question_toolcall_is_blocked_on_wechat(self):
        controller = _StubController(lang="zh", platform="wechat")
        im_client = _StubIMClient()
        handler = OpenCodeQuestionHandler(controller, im_client, object())
        server = _StubServer(ok=True)
        request = _build_request("ask")
        seen_tool_calls = set()

        answered = await handler.handle_question_toolcall(
            request=request,
            server=server,
            opencode_session_id="oc-session",
            message_id="msg-1",
            tool_part={"callID": "call-1"},
            tool_input={"questions": [{"question": "Need input?"}]},
            call_key="call-1",
            seen_tool_calls=seen_tool_calls,
        )

        self.assertFalse(answered)
        self.assertEqual(server.abort_calls, [("oc-session", "/tmp/work")])
        self.assertEqual(
            controller.emitted_messages,
            [("C123", "notify", "微信平台暂时不支持 OpenCode 的交互式提问。请直接再发一条消息继续。", None)],
        )
        self.assertEqual(im_client.sent_messages, [])
        self.assertIn("call-1", seen_tool_calls)


class QuestionUIHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_answer_receipt_formats_multi_question_answers(self):
        controller = _StubController(lang="en")
        im_client = _StubIMClient()
        ui_handler = QuestionUIHandler(controller, im_client, object(), "opencode_question")
        request = _build_request("unused")

        await ui_handler.send_answer_receipt(request, [["Alpha", "Beta"], ["Gamma"]])

        self.assertEqual(im_client.sent_messages[0][2], "Reply: \nQ1: Alpha, Beta\nQ2: Gamma")

    async def test_update_prompt_after_answer_does_not_append_selection_text(self):
        controller = _StubController(lang="zh")
        im_client = _StubIMClient()
        ui_handler = QuestionUIHandler(controller, im_client, object(), "opencode_question")
        request = _build_request("freeform answer")
        pending = PendingQuestion(
            questions=[],
            prompt_text="请选择一个答案",
            option_labels=[],
            base_session_id=request.base_session_id,
            thread_id=request.context.thread_id,
            prompt_message_id="PROMPT2",
        )

        await ui_handler.update_prompt_after_answer(request, pending)

        self.assertEqual(
            im_client.removed_keyboards,
            [("C123", "T123", "PROMPT2", "请选择一个答案", "markdown")],
        )
        self.assertNotIn("已选择", im_client.removed_keyboards[0][3])
