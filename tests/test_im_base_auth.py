import asyncio

from modules.im import MessageContext
from modules.im.base import BaseIMClient, BaseIMConfig


class _Cfg(BaseIMConfig):
    def validate(self) -> None:
        return None


class _IM(BaseIMClient):
    async def send_message(self, context, text, parse_mode=None, reply_to=None):
        return ""

    async def send_message_with_buttons(self, context, text, keyboard, parse_mode=None):
        return ""

    async def edit_message(self, context, message_id, text=None, keyboard=None, parse_mode=None):
        return True

    async def answer_callback(self, callback_id, text=None, show_alert=False):
        return True

    def register_handlers(self):
        return None

    def run(self):
        return None

    async def get_user_info(self, user_id):
        return {}

    async def get_channel_info(self, channel_id):
        return {}

    def format_markdown(self, text):
        return text


def test_extract_command_action():
    assert BaseIMClient.extract_command_action("/settings") == "settings"
    assert BaseIMClient.extract_command_action("/set_cwd /tmp") == "set_cwd"
    assert BaseIMClient.extract_command_action("hello") == ""
    assert BaseIMClient.extract_command_action("") == ""


def test_parse_text_command():
    assert BaseIMClient.parse_text_command("/settings") == ("settings", "")
    assert BaseIMClient.parse_text_command("/set_cwd /tmp") == ("set_cwd", "/tmp")
    assert BaseIMClient.parse_text_command("hello") is None
    assert BaseIMClient.parse_text_command("/") is None


def test_check_authorization_uses_text_when_action_missing():
    im = _IM(_Cfg())
    result = im.check_authorization(
        user_id="U1",
        channel_id="D1",
        is_dm=True,
        text="/bind code",
    )
    assert result.allowed is True


def test_dispatch_text_command_executes_handler():
    im = _IM(_Cfg())
    received = {}

    async def _handler(context, args):
        received["user"] = context.user_id
        received["args"] = args

    im.on_command_callbacks = {"start": _handler}
    context = MessageContext(user_id="U1", channel_id="C1")

    handled = asyncio.run(im.dispatch_text_command(context, "/start now"))

    assert handled is True
    assert received == {"user": "U1", "args": "now"}


def test_dispatch_text_command_returns_false_for_unknown():
    im = _IM(_Cfg())
    context = MessageContext(user_id="U1", channel_id="C1")
    handled = asyncio.run(im.dispatch_text_command(context, "/unknown"))
    assert handled is False
