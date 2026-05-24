"""Type helpers for OpenCode agent implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class ModelDict(TypedDict):
    providerID: str
    modelID: str


@dataclass(frozen=True)
class RequestSessionInfo:
    opencode_session_id: str
    working_path: str
    session_key: str
