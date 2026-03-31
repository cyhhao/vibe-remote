from __future__ import annotations

from datetime import datetime

from .base import build_tail_preview
from .types import NativeResumeSession


def format_display_time(item: NativeResumeSession) -> str:
    dt = item.updated_at or item.created_at
    if not dt:
        return "--"
    now = datetime.now()
    if dt.year == now.year:
        return dt.strftime("%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d")


def format_display_summary(item: NativeResumeSession) -> str:
    tail = item.last_agent_tail or build_tail_preview(item.native_session_id)
    suffix = tail.lstrip(".") or item.native_session_id[-10:]
    return f"{item.agent_prefix}...{suffix}"
