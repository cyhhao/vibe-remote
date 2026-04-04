from __future__ import annotations

from types import SimpleNamespace

from core.message_dispatcher import ConsolidatedMessageDispatcher
from modules.im import MessageContext
from tests.scenario_harness.core import BaseScenarioHarness, ScenarioControllerBase


class MessageDeliverySettingsManager:
    def _canonicalize_message_type(self, message_type):
        return message_type

    def is_message_type_hidden(self, settings_key, canonical_type):
        return False


class MessageDeliverySessionHandler:
    def __init__(self):
        self.finalized = []

    def finalize_scheduled_delivery(self, context, sent_message_id):
        self.finalized.append((context.channel_id, context.thread_id, sent_message_id))


class MessageDeliveryController(ScenarioControllerBase):
    def __init__(self, *, platform: str = "slack"):
        super().__init__(default_backend="codex", platform=platform)
        self.config.reply_enhancements = False
        self.session_handler = MessageDeliverySessionHandler()
        self.settings_manager = MessageDeliverySettingsManager()

    def _get_session_key(self, context):
        return f"{context.platform or self.config.platform}::{context.channel_id}"

    def get_settings_manager_for_context(self, context):
        return self.settings_manager


class MessageDeliveryHarness(BaseScenarioHarness):
    def __init__(self, *, platform: str = "slack", user_id: str = "scheduled", channel_id: str = "C123", thread_id=None):
        super().__init__(
            MessageDeliveryController(platform=platform),
            user_id=user_id,
            channel_id=channel_id,
        )
        self.context.thread_id = thread_id
        self.context.platform = platform
        self.dispatcher = ConsolidatedMessageDispatcher(self.controller)

    async def emit_result(self, text: str):
        return await self.dispatcher.emit_agent_message(self.context, "result", text)

    @property
    def sent_messages(self):
        return list(getattr(self.controller.im_client, "probe").matching("message"))

    @property
    def finalized_calls(self):
        return list(self.controller.session_handler.finalized)
