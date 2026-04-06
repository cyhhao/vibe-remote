import asyncio

import pytest

import modules.claude_sdk_compat as compat


pytestmark = pytest.mark.skipif(
    not compat.CLAUDE_SDK_AVAILABLE,
    reason="claude_agent_sdk is not installed",
)


class _FakeQuery:
    def __init__(self, messages):
        self._messages = messages

    async def receive_messages(self):
        for message in self._messages:
            yield message


async def _collect_messages(messages):
    client = compat.ClaudeSDKClient()
    client._query = _FakeQuery(messages)
    return [message async for message in client.receive_messages()]


def test_receive_messages_skips_rate_limit_event():
    messages = asyncio.run(
        _collect_messages(
            [
                {"type": "rate_limit_event", "retry_after_ms": 1000},
                {"type": "system", "subtype": "init", "cwd": "/tmp"},
            ]
        )
    )

    assert len(messages) == 1
    assert isinstance(messages[0], compat.SystemMessage)
    assert messages[0].subtype == "init"


def test_receive_messages_still_raises_for_other_unknown_types():
    from claude_agent_sdk._errors import MessageParseError

    with pytest.raises(MessageParseError, match="Unknown message type: mystery_event"):
        asyncio.run(_collect_messages([{"type": "mystery_event"}]))
