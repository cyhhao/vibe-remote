import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from config import paths

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    session_mappings: Dict[str, Dict[str, Dict[str, Dict[str, str]]]] = field(
        default_factory=dict
    )
    active_slack_threads: Dict[str, Dict[str, Dict[str, float]]] = field(
        default_factory=dict
    )
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
            last_activity=payload.get("last_activity"),
        )

    def _ensure_user_namespace(self, user_id: str) -> None:
        if user_id not in self.state.session_mappings:
            self.state.session_mappings[user_id] = {}
        if user_id not in self.state.active_slack_threads:
            self.state.active_slack_threads[user_id] = {}

    def get_agent_map(self, user_id: str, agent_name: str) -> Dict[str, Dict[str, str]]:
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

    def save(self) -> None:
        paths.ensure_data_dirs()
        payload = {
            "session_mappings": self.state.session_mappings,
            "active_slack_threads": self.state.active_slack_threads,
            "last_activity": self.state.last_activity,
        }
        self.sessions_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
