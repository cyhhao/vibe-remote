"""Type helpers for OpenCode agent implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypedDict


class ModelDict(TypedDict):
    providerID: str
    modelID: str


class PendingQuestionPayload(TypedDict, total=False):
    session_id: str
    directory: str
    question_id: Optional[str]
    call_id: Optional[str]
    message_id: Optional[str]
    prompt_message_id: Optional[str]
    prompt_text: str
    option_labels: List[str]
    question_count: int
    multiple: bool
    questions: List[Dict[str, Any]]
    thread_id: Optional[str]
    trigger_message_id: Optional[str]  # Original request message_id for consolidated key


@dataclass(frozen=True)
class RequestSessionInfo:
    opencode_session_id: str
    working_path: str
    settings_key: str
