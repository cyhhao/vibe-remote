"""Facade for runtime session/thread/dedup/poll state.

This facade centralizes runtime conversation state operations that are
backed by ``config.v2_sessions.SessionsStore``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Union

from config.v2_sessions import ActivePollInfo, SessionsStore

logger = logging.getLogger(__name__)


class SessionsFacade:
    """High-level APIs for session and runtime state operations."""

    def __init__(self, sessions_store: SessionsStore):
        self.sessions_store = sessions_store

    def _normalize_user_id(self, user_id: Union[int, str]) -> str:
        return str(user_id)

    def _ensure_agent_namespace(self, user_id: Union[int, str], agent_name: str) -> Dict[str, str]:
        user_key = self._normalize_user_id(user_id)
        return self.sessions_store.get_agent_map(user_key, agent_name)

    def set_agent_session_mapping(
        self,
        user_id: Union[int, str],
        agent_name: str,
        thread_id: str,
        session_id: str,
    ) -> None:
        agent_map = self._ensure_agent_namespace(user_id, agent_name)
        agent_map[thread_id] = session_id
        self.sessions_store.save()
        logger.info("Stored %s session mapping for %s: %s -> %s", agent_name, user_id, thread_id, session_id)

    def get_agent_session_id(
        self,
        user_id: Union[int, str],
        thread_id: str,
        agent_name: str,
    ) -> Optional[str]:
        user_key = self._normalize_user_id(user_id)
        agent_map = self.sessions_store.get_agent_map(user_key, agent_name)
        return agent_map.get(thread_id)

    def clear_agent_session_mapping(
        self,
        user_id: Union[int, str],
        agent_name: str,
        thread_id: str,
    ) -> None:
        user_key = self._normalize_user_id(user_id)
        agent_map = self.sessions_store.get_agent_map(user_key, agent_name)
        if thread_id in agent_map:
            del agent_map[thread_id]
            self.sessions_store.save()
            logger.info("Cleared %s session mapping for user %s: %s", agent_name, user_id, thread_id)

    def clear_agent_sessions(self, user_id: Union[int, str], agent_name: str) -> None:
        user_key = self._normalize_user_id(user_id)
        agent_map = self.sessions_store.get_agent_map(user_key, agent_name)
        if agent_map:
            self.sessions_store.state.session_mappings[user_key][agent_name] = {}
            self.sessions_store.save()
            logger.info("Cleared all %s session namespaces for user %s", agent_name, user_id)

    def clear_all_session_mappings(self, user_id: Union[int, str]) -> None:
        user_key = self._normalize_user_id(user_id)
        agent_maps = self.sessions_store.state.session_mappings.get(user_key, {})
        if agent_maps:
            count = sum(len(agent_map) for agent_map in agent_maps.values())
            self.sessions_store.state.session_mappings[user_key] = {}
            self.sessions_store.save()
            logger.info("Cleared all session mappings (%s bases) for user %s", count, user_id)

    def list_agent_sessions(self, user_id: Union[int, str], agent_name: str) -> Dict[str, str]:
        user_key = self._normalize_user_id(user_id)
        agent_map = self.sessions_store.get_agent_map(user_key, agent_name)
        return dict(agent_map)

    def list_all_agent_sessions(self, user_id: Union[int, str]) -> Dict[str, Dict[str, str]]:
        user_key = self._normalize_user_id(user_id)
        self.sessions_store._ensure_user_namespace(user_key)
        agent_maps = self.sessions_store.state.session_mappings.get(user_key, {})
        return {agent: dict(mapping) for agent, mapping in agent_maps.items()}

    @staticmethod
    def _matches_base_prefix(mapping_key: str, base_session_id: str) -> bool:
        return mapping_key == base_session_id or mapping_key.startswith(f"{base_session_id}:")

    def has_any_agent_session_base(self, user_id: Union[int, str], base_session_id: str) -> bool:
        user_key = self._normalize_user_id(user_id)
        self.sessions_store._ensure_user_namespace(user_key)
        agent_maps = self.sessions_store.state.session_mappings.get(user_key, {})
        for agent_map in agent_maps.values():
            for mapping_key in agent_map.keys():
                if self._matches_base_prefix(mapping_key, base_session_id):
                    return True
        return False

    def alias_session_base(
        self,
        user_id: Union[int, str],
        source_base_session_id: str,
        alias_base_session_id: str,
    ) -> bool:
        user_key = self._normalize_user_id(user_id)
        self.sessions_store._ensure_user_namespace(user_key)
        agent_maps = self.sessions_store.state.session_mappings.get(user_key, {})
        changed = False

        for agent_name, agent_map in agent_maps.items():
            additions: Dict[str, str] = {}
            for mapping_key, native_session_id in list(agent_map.items()):
                if not self._matches_base_prefix(mapping_key, source_base_session_id):
                    continue
                suffix = mapping_key[len(source_base_session_id) :]
                alias_key = f"{alias_base_session_id}{suffix}"
                if alias_key in agent_map or alias_key in additions:
                    continue
                additions[alias_key] = native_session_id
            if additions:
                agent_map.update(additions)
                changed = True
                logger.info(
                    "Aliased %s session base for %s: %s -> %s (%s keys)",
                    agent_name,
                    user_key,
                    source_base_session_id,
                    alias_base_session_id,
                    len(additions),
                )

        if changed:
            self.sessions_store.save()
        return changed

    def clear_session_base(self, user_id: Union[int, str], base_session_id: str) -> int:
        user_key = self._normalize_user_id(user_id)
        self.sessions_store._ensure_user_namespace(user_key)
        agent_maps = self.sessions_store.state.session_mappings.get(user_key, {})
        cleared = 0

        for agent_name, agent_map in agent_maps.items():
            keys_to_remove = [
                mapping_key for mapping_key in list(agent_map.keys()) if self._matches_base_prefix(mapping_key, base_session_id)
            ]
            if not keys_to_remove:
                continue
            for mapping_key in keys_to_remove:
                del agent_map[mapping_key]
                cleared += 1
            logger.info(
                "Cleared %s session base for %s: %s (%s keys)",
                agent_name,
                user_key,
                base_session_id,
                len(keys_to_remove),
            )

        if cleared:
            self.sessions_store.save()
        return cleared

    def get_all_session_mappings(self) -> Dict[str, Dict[str, Dict[str, str]]]:
        """Return all persisted session mappings grouped by user and agent."""
        mappings = self.sessions_store.state.session_mappings
        return {
            user_id: {agent: dict(agent_map) for agent, agent_map in (agents or {}).items()}
            for user_id, agents in mappings.items()
        }

    def set_session_mapping(self, user_id: Union[int, str], thread_id: str, claude_session_id: str) -> None:
        self.set_agent_session_mapping(user_id, "claude", thread_id, claude_session_id)

    def get_claude_session_id(self, user_id: Union[int, str], thread_id: str) -> Optional[str]:
        return self.get_agent_session_id(user_id, thread_id, agent_name="claude")

    def clear_session_mapping(self, user_id: Union[int, str], thread_id: str) -> None:
        self.clear_agent_session_mapping(user_id, "claude", thread_id)

    def mark_thread_active(self, user_id: Union[int, str], channel_id: str, thread_ts: str) -> None:
        user_key = self._normalize_user_id(user_id)
        channel_map = self.sessions_store.get_thread_map(user_key, channel_id)
        channel_map[thread_ts] = time.time()
        self.sessions_store.save()
        logger.info("Marked thread active for user %s: channel=%s, thread=%s", user_id, channel_id, thread_ts)

    def is_thread_active(self, user_id: Union[int, str], channel_id: str, thread_ts: str) -> bool:
        user_key = self._normalize_user_id(user_id)
        self._cleanup_expired_threads_for_channel(user_id, channel_id)
        channel_map = self.sessions_store.get_thread_map(user_key, channel_id)
        return thread_ts in channel_map

    def _cleanup_expired_threads_for_channel(self, user_id: Union[int, str], channel_id: str) -> None:
        user_key = self._normalize_user_id(user_id)
        channel_map = self.sessions_store.get_thread_map(user_key, channel_id)
        if not channel_map:
            return

        current_time = time.time()
        twenty_four_hours_ago = current_time - (24 * 60 * 60)
        expired_threads = [
            thread_ts for thread_ts, last_active in channel_map.items() if last_active < twenty_four_hours_ago
        ]

        if not expired_threads:
            return

        for thread_ts in expired_threads:
            del channel_map[thread_ts]

        if not channel_map:
            self.sessions_store.state.active_slack_threads[user_key].pop(channel_id, None)

        self.sessions_store.save()
        logger.info("Cleaned up %s expired threads for channel %s", len(expired_threads), channel_id)

    def cleanup_all_expired_threads(self, user_id: Union[int, str]) -> None:
        user_key = self._normalize_user_id(user_id)
        channel_map = self.sessions_store.state.active_slack_threads.get(user_key, {})
        if not channel_map:
            return
        for channel_id in list(channel_map.keys()):
            self._cleanup_expired_threads_for_channel(user_id, channel_id)

    def is_message_already_processed(self, channel_id: str, thread_ts: str, message_ts: str) -> bool:
        return self.sessions_store.is_message_in_processed_set(channel_id, thread_ts, message_ts)

    def record_processed_message(self, channel_id: str, thread_ts: str, message_ts: str) -> None:
        self.sessions_store.add_to_processed_set(channel_id, thread_ts, message_ts)
        logger.debug("Recorded processed message: channel=%s, thread=%s, message=%s", channel_id, thread_ts, message_ts)

    def add_active_poll(
        self,
        opencode_session_id: str,
        base_session_id: str,
        channel_id: str,
        thread_id: str,
        settings_key: str,
        working_path: str,
        baseline_message_ids: List[str],
        ack_reaction_message_id: Optional[str] = None,
        ack_reaction_emoji: Optional[str] = None,
        user_id: str = "",
        platform: str = "",
    ) -> None:
        poll_info = ActivePollInfo(
            opencode_session_id=opencode_session_id,
            base_session_id=base_session_id,
            channel_id=channel_id,
            thread_id=thread_id,
            settings_key=settings_key,
            working_path=working_path,
            baseline_message_ids=baseline_message_ids,
            seen_tool_calls=[],
            emitted_assistant_messages=[],
            started_at=time.time(),
            ack_reaction_message_id=ack_reaction_message_id,
            ack_reaction_emoji=ack_reaction_emoji,
            user_id=user_id,
            platform=platform,
        )
        self.sessions_store.add_active_poll(poll_info)
        logger.debug("Added active poll: session=%s, thread=%s", opencode_session_id, thread_id)

    def remove_active_poll(self, opencode_session_id: str) -> None:
        self.sessions_store.remove_active_poll(opencode_session_id)
        logger.debug("Removed active poll: session=%s", opencode_session_id)

    def update_active_poll_state(
        self,
        opencode_session_id: str,
        seen_tool_calls: Optional[List[str]] = None,
        emitted_assistant_messages: Optional[List[str]] = None,
    ) -> None:
        poll_info = self.sessions_store.get_active_poll(opencode_session_id)
        if poll_info:
            if seen_tool_calls is not None:
                poll_info.seen_tool_calls = seen_tool_calls
            if emitted_assistant_messages is not None:
                poll_info.emitted_assistant_messages = emitted_assistant_messages
            self.sessions_store.update_active_poll(poll_info)

    def get_all_active_polls(self) -> Dict[str, Any]:
        return self.sessions_store.get_all_active_polls()
