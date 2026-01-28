import unittest

from core.controller import Controller
from core.handlers.command_handlers import CommandHandlers
from modules.im import MessageContext
from modules.im.slack import SlackBot
from config.v2_config import SlackConfig


class _StubSettingsManager:
    def __init__(self):
        self.set_calls = []
        self.mark_calls = []
        self.routing_calls = []

    def set_agent_session_mapping(self, settings_key, agent_name, thread_id, session_id):
        self.set_calls.append((settings_key, agent_name, thread_id, session_id))

    def mark_thread_active(self, user_id, channel_id, thread_ts):
        self.mark_calls.append((user_id, channel_id, thread_ts))

    def list_all_agent_sessions(self, user_id):
        return {}

    def get_channel_routing(self, settings_key):
        return None

    def set_channel_routing(self, settings_key, routing):
        self.routing_calls.append((settings_key, routing))


class _StubIMClient:
    def __init__(self):
        self.messages = []
        self.resume_calls = []

    async def send_message(self, context, text):
        ts = f"T{len(self.messages) + 1}"
        self.messages.append((context.channel_id, context.thread_id, text, ts))
        return ts

    async def open_resume_session_modal(self, trigger_id, sessions_by_agent, channel_id, thread_id, host_message_ts):
        self.resume_calls.append((trigger_id, sessions_by_agent, channel_id, thread_id, host_message_ts))


class _StubConfig:
    def __init__(self, platform="slack"):
        self.platform = platform


class _StubController(Controller):
    def __init__(self):
        # Bypass base __init__ to avoid wiring everything
        pass

    def init_minimal(self, im_client, settings_manager, config, session_manager=None):
        self.im_client = im_client
        self.settings_manager = settings_manager
        self.config = config
        self.session_manager = session_manager
        self.command_handler = CommandHandlers(self)

    def _get_settings_key(self, context: MessageContext) -> str:
        return context.channel_id


class ResumeSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_resume_session_submission_threads(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())

        await ctrl.handle_resume_session_submission(
            user_id="U123",
            channel_id="C111",
            thread_id="169999.123",
            agent="claude",
            session_id="sess_abc",
        )

        self.assertEqual(
            settings.set_calls,
            [("C111", "claude", "slack_169999.123", "sess_abc")],
        )
        self.assertEqual(settings.mark_calls, [("U123", "C111", "169999.123")])
        self.assertIn("sess_abc", im_client.messages[0][2])

    async def test_handle_resume_session_submission_dm_falls_back_to_channel(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig())

        await ctrl.handle_resume_session_submission(
            user_id="U999",
            channel_id="DXYZ",
            thread_id=None,
            agent="codex",
            session_id="sess_dm",
        )

        # No thread provided -> new confirmation message anchor used
        self.assertEqual(settings.set_calls, [("DXYZ", "codex", "slack_T1", "sess_dm")])
        self.assertEqual(settings.mark_calls, [("U999", "DXYZ", "T1")])

    async def test_command_handlers_handle_resume_opens_modal(self):
        settings = _StubSettingsManager()
        im_client = _StubIMClient()
        ctrl = _StubController()
        ctrl.init_minimal(im_client, settings, _StubConfig(platform="slack"))

        ctx = MessageContext(
            user_id="U1",
            channel_id="CCHAN",
            thread_id="TH1",
            message_id="TS1",
            platform_specific={"trigger_id": "TRIG"},
        )

        await ctrl.command_handler.handle_resume(ctx)

        # One info message about missing sessions
        self.assertEqual(len(im_client.messages), 1)
        self.assertIn("No stored sessions", im_client.messages[0][2])
        # Modal opened with empty sessions map and host ts
        self.assertEqual(im_client.resume_calls, [("TRIG", {}, "CCHAN", "TH1", "TS1")])

    async def test_resume_modal_manual_session_uses_manual_agent(self):
        cfg = SlackConfig(bot_token="xoxb-test")
        slack = SlackBot(cfg)
        received = {}

        async def _on_resume(user_id, channel_id, thread_id, agent, session, host_ts):
            received["args"] = (user_id, channel_id, thread_id, agent, session, host_ts)

        slack._on_resume_session = _on_resume

        payload = {
            "type": "view_submission",
            "user": {"id": "U1"},
            "view": {
                "callback_id": "resume_session_modal",
                "state": {
                    "values": {
                        "agent_block": {"agent_select": {"selected_option": {"value": "codex"}}},
                        "manual_block": {"manual_input": {"value": "manual_sess"}},
                        "session_block": {"session_select": {"selected_option": {"value": "claude|sess_drop"}}},
                    }
                },
                "private_metadata": ('{"channel_id":"C1","thread_id":"TH1","host_message_ts":"TS1"}'),
            },
        }

        await slack._handle_view_submission(payload)

        self.assertEqual(
            received["args"],
            ("U1", "C1", "TH1", "codex", "manual_sess", "TS1"),
        )


if __name__ == "__main__":
    unittest.main()
