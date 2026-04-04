from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.scenario_harness.core import ScenarioExpect, ScenarioRunner, ScenarioStep
from tests.scenario_harness.message_delivery import MessageDeliveryHarness


class MessageDeliveryScenarioTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduled_result_delivery_scenario_finalizes_anchor(self):
        harness = MessageDeliveryHarness(platform="slack")
        harness.context.platform_specific = {
            "turn_source": "scheduled",
            "turn_base_session_id": "slack_scheduled-1",
            "scheduled_anchor_required": True,
        }
        runner = ScenarioRunner(harness)

        await runner.run(
            ScenarioStep("emit_result", lambda h: h.emit_result("hello")),
        )

        ScenarioExpect.step_history(runner, ["emit_result"])
        ScenarioExpect.text_contains(harness, "hello")
        self.assertEqual(harness.finalized_calls, [("C123", None, "msg-1")])

    async def test_scheduled_result_delivery_override_scenario_uses_parent_channel_target(self):
        harness = MessageDeliveryHarness(platform="slack", thread_id="171717.123")
        harness.context.platform_specific = {
            "turn_source": "scheduled",
            "turn_base_session_id": "slack_171717.123",
            "delivery_override": {
                "user_id": "scheduled",
                "channel_id": "C123",
                "thread_id": None,
                "platform": "slack",
                "is_dm": False,
            },
            "scheduled_delivery_alias": {
                "mode": "sent_message",
                "session_key": "slack::C123",
                "clear_source": False,
            },
        }
        runner = ScenarioRunner(harness)

        await runner.run(
            ScenarioStep("emit_result", lambda h: h.emit_result("hello")),
        )

        ScenarioExpect.step_history(runner, ["emit_result"])
        ScenarioExpect.text_contains(harness, "hello")
        self.assertEqual(harness.finalized_calls, [("C123", "171717.123", "msg-1")])


if __name__ == "__main__":
    unittest.main()
