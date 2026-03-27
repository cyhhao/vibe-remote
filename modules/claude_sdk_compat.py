"""Compatibility helpers for environments without claude_agent_sdk installed."""

from __future__ import annotations


try:
    from claude_agent_sdk import (  # type: ignore[import-not-found]
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )
    CLAUDE_SDK_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal test envs
    CLAUDE_SDK_AVAILABLE = False

    class _MissingClaudeSDK:
        def __init__(self, *args, **kwargs):
            raise ModuleNotFoundError("claude_agent_sdk is required for Claude backend functionality")

    class ClaudeAgentOptions(_MissingClaudeSDK):
        pass

    class ClaudeSDKClient(_MissingClaudeSDK):
        pass

    class SystemMessage:  # minimal placeholders for isinstance checks
        pass

    class AssistantMessage:
        pass

    class UserMessage:
        pass

    class ResultMessage:
        pass

    class TextBlock:
        pass

    class ToolUseBlock:
        pass

    class ToolResultBlock:
        pass

