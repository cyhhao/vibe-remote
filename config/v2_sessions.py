import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import paths

logger = logging.getLogger(__name__)


@dataclass
class ActivePollInfo:
    """Information about an active poll that needs to be restored on restart."""

    opencode_session_id: str
    base_session_id: str
    channel_id: str
    thread_id: str
    settings_key: str
    working_path: str
    baseline_message_ids: List[str] = field(default_factory=list)
    seen_tool_calls: List[str] = field(default_factory=list)
    emitted_assistant_messages: List[str] = field(default_factory=list)
    started_at: float = 0.0
    # Ack reaction info for cleanup on restore
    ack_reaction_message_id: Optional[str] = None
    ack_reaction_emoji: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "opencode_session_id": self.opencode_session_id,
            "base_session_id": self.base_session_id,
            "channel_id": self.channel_id,
            "thread_id": self.thread_id,
            "settings_key": self.settings_key,
            "working_path": self.working_path,
            "baseline_message_ids": self.baseline_message_ids,
            "seen_tool_calls": self.seen_tool_calls,
            "emitted_assistant_messages": self.emitted_assistant_messages,
            "started_at": self.started_at,
            "ack_reaction_message_id": self.ack_reaction_message_id,
            "ack_reaction_emoji": self.ack_reaction_emoji,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActivePollInfo":
        return cls(
            opencode_session_id=data.get("opencode_session_id", ""),
            base_session_id=data.get("base_session_id", ""),
            channel_id=data.get("channel_id", ""),
            thread_id=data.get("thread_id", ""),
            settings_key=data.get("settings_key", ""),
            working_path=data.get("working_path", ""),
            baseline_message_ids=data.get("baseline_message_ids", []),
            seen_tool_calls=data.get("seen_tool_calls", []),
            emitted_assistant_messages=data.get("emitted_assistant_messages", []),
            started_at=data.get("started_at", 0.0),
            ack_reaction_message_id=data.get("ack_reaction_message_id"),
            ack_reaction_emoji=data.get("ack_reaction_emoji"),
        )


@dataclass
class SessionState:
    # session_mappings: user_id -> agent_name -> thread_id -> session_id
    session_mappings: Dict[str, Dict[str, Dict[str, str]]] = field(default_factory=dict)
    active_slack_threads: Dict[str, Dict[str, Dict[str, float]]] = field(
        default_factory=dict
    )
    # active_polls: opencode_session_id -> ActivePollInfo
    active_polls: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # processed_message_ts: channel_id -> thread_ts -> last_processed_message_ts
    processed_message_ts: Dict[str, Dict[str, str]] = field(default_factory=dict)
    last_activity: Optional[str] = None


@dataclass
class SessionsStore:
    sessions_path: Path = field(default_factory=paths.get_sessions_path)
    state: SessionState = field(default_factory=SessionState)

    def load(self) -> None:
        if not self.sessions_path.exists():
            return
        try:
            payload = json.loads(self.sessions_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to load sessions: %s", exc)
            return
        self.state = SessionState(
            session_mappings=payload.get("session_mappings", {}),
            active_slack_threads=payload.get("active_slack_threads", {}),
            active_polls=payload.get("active_polls", {}),
            processed_message_ts=payload.get("processed_message_ts", {}),
            last_activity=payload.get("last_activity"),
        )

    def _ensure_user_namespace(self, user_id: str) -> None:
        if user_id not in self.state.session_mappings:
            self.state.session_mappings[user_id] = {}
        if user_id not in self.state.active_slack_threads:
            self.state.active_slack_threads[user_id] = {}

    def get_agent_map(self, user_id: str, agent_name: str) -> Dict[str, str]:
        """Get mapping of thread_id -> session_id for a user and agent."""
        self._ensure_user_namespace(user_id)
        agent_map = self.state.session_mappings[user_id].get(agent_name)
        if agent_map is None:
            agent_map = {}
            self.state.session_mappings[user_id][agent_name] = agent_map
        return agent_map

    def get_thread_map(self, user_id: str, channel_id: str) -> Dict[str, float]:
        self._ensure_user_namespace(user_id)
        channel_map = self.state.active_slack_threads[user_id].get(channel_id)
        if channel_map is None:
            channel_map = {}
            self.state.active_slack_threads[user_id][channel_id] = channel_map
        return channel_map

    def get_last_processed_message_ts(
        self, channel_id: str, thread_ts: str
    ) -> Optional[str]:
        """Get the last processed message ts for a thread."""
        channel_map = self.state.processed_message_ts.get(channel_id)
        if channel_map:
            return channel_map.get(thread_ts)
        return None

    def set_last_processed_message_ts(
        self, channel_id: str, thread_ts: str, message_ts: str
    ) -> None:
        """Set the last processed message ts for a thread."""
        if channel_id not in self.state.processed_message_ts:
            self.state.processed_message_ts[channel_id] = {}
        self.state.processed_message_ts[channel_id][thread_ts] = message_ts
        self.save()

    def add_active_poll(self, poll_info: ActivePollInfo) -> None:
        """Add an active poll to track."""
        self.state.active_polls[poll_info.opencode_session_id] = poll_info.to_dict()
        self.save()

    def remove_active_poll(self, opencode_session_id: str) -> None:
        """Remove an active poll."""
        if opencode_session_id in self.state.active_polls:
            del self.state.active_polls[opencode_session_id]
            self.save()

    def get_active_poll(self, opencode_session_id: str) -> Optional[ActivePollInfo]:
        """Get active poll info by session ID."""
        data = self.state.active_polls.get(opencode_session_id)
        if data:
            return ActivePollInfo.from_dict(data)
        return None

    def get_all_active_polls(self) -> Dict[str, ActivePollInfo]:
        """Get all active polls."""
        return {
            sid: ActivePollInfo.from_dict(data)
            for sid, data in self.state.active_polls.items()
        }

    def update_active_poll(self, poll_info: ActivePollInfo) -> None:
        """Update an existing active poll."""
        if poll_info.opencode_session_id in self.state.active_polls:
            self.state.active_polls[poll_info.opencode_session_id] = poll_info.to_dict()
            self.save()

    def save(self) -> None:
        paths.ensure_data_dirs()
        payload = {
            "session_mappings": self.state.session_mappings,
            "active_slack_threads": self.state.active_slack_threads,
            "active_polls": self.state.active_polls,
            "processed_message_ts": self.state.processed_message_ts,
            "last_activity": self.state.last_activity,
        }
        self.sessions_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
