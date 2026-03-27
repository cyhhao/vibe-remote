from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime

from .base import NativeSessionProvider, build_tail_preview
from .claude import ClaudeNativeSessionProvider
from .codex import CodexNativeSessionProvider
from .opencode import OpenCodeNativeSessionProvider
from .types import NativeResumeSession

logger = logging.getLogger(__name__)


class AgentNativeSessionService:
    def __init__(self, providers: list[NativeSessionProvider] | None = None):
        self.providers = providers or [
            OpenCodeNativeSessionProvider(),
            ClaudeNativeSessionProvider(),
            CodexNativeSessionProvider(),
        ]

    def list_recent_sessions(self, working_path: str, limit: int = 100) -> list[NativeResumeSession]:
        items: list[NativeResumeSession] = []
        for provider in self.providers:
            try:
                items.extend(provider.list_metadata(working_path))
            except Exception as exc:
                logger.warning("Failed to list %s sessions for %s: %s", provider.agent_name, working_path, exc)

        items.sort(key=lambda item: (-item.sort_ts, item.agent_prefix, item.native_session_id))
        items = self._apply_limit(items, max(limit, 0))

        provider_by_name = {provider.agent_name: provider for provider in self.providers}
        hydrated: list[NativeResumeSession] = []
        for item in items:
            provider = provider_by_name.get(item.agent)
            if not provider:
                hydrated.append(item)
                continue
            try:
                hydrated.append(provider.hydrate_preview(item))
            except Exception as exc:
                logger.warning("Failed to hydrate %s session %s: %s", item.agent, item.native_session_id, exc)
                hydrated.append(item)
        return hydrated

    @staticmethod
    def _apply_limit(items: list[NativeResumeSession], limit: int) -> list[NativeResumeSession]:
        if limit <= 0:
            return []
        if len(items) <= limit:
            return items

        selected = list(items[:limit])
        present_agents = {item.agent for item in selected}
        missing_agents = []
        seen_missing = set()
        for item in items[limit:]:
            if item.agent in present_agents or item.agent in seen_missing:
                continue
            missing_agents.append(item)
            seen_missing.add(item.agent)

        if not missing_agents:
            return selected

        counts = Counter(item.agent for item in selected)
        for replacement in missing_agents:
            removable_index = None
            for index in range(len(selected) - 1, -1, -1):
                candidate = selected[index]
                if counts[candidate.agent] > 1:
                    removable_index = index
                    break
            if removable_index is None:
                removable_index = len(selected) - 1
            removed = selected[removable_index]
            counts[removed.agent] -= 1
            selected[removable_index] = replacement
            counts[replacement.agent] += 1

        selected.sort(key=lambda item: (-item.sort_ts, item.agent_prefix, item.native_session_id))
        return selected

    @staticmethod
    def format_display_time(item: NativeResumeSession) -> str:
        dt = item.updated_at or item.created_at
        if not dt:
            return "--"
        now = datetime.now()
        if dt.year == now.year:
            return dt.strftime("%m-%d %H:%M")
        return dt.strftime("%Y-%m-%d")

    @staticmethod
    def format_display_summary(item: NativeResumeSession) -> str:
        tail = item.last_agent_tail or build_tail_preview(item.native_session_id)
        suffix = tail.lstrip(".") or item.native_session_id[-10:]
        return f"{item.agent_prefix}...{suffix}"

    def get_session(
        self,
        working_path: str,
        agent: str,
        native_session_id: str,
    ) -> NativeResumeSession | None:
        provider_by_name = {provider.agent_name: provider for provider in self.providers}
        provider = provider_by_name.get(agent)
        if not provider:
            return None
        try:
            items = provider.list_metadata(working_path)
        except Exception as exc:
            logger.warning("Failed to list %s sessions for preview lookup: %s", agent, exc)
            return None
        for item in items:
            if item.native_session_id != native_session_id:
                continue
            try:
                return provider.hydrate_preview(item)
            except Exception as exc:
                logger.warning("Failed to hydrate %s session %s: %s", agent, native_session_id, exc)
                return item
        return None
