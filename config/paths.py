import os
from pathlib import Path


def get_vibe_remote_dir() -> Path:
    custom = os.environ.get("VIBE_REMOTE_HOME")
    if custom:
        return Path(custom).expanduser().resolve()
    return Path.home() / ".vibe_remote"


def get_config_dir() -> Path:
    return get_vibe_remote_dir() / "config"


def get_state_dir() -> Path:
    return get_vibe_remote_dir() / "state"


def get_logs_dir() -> Path:
    return get_vibe_remote_dir() / "logs"


def get_runtime_dir() -> Path:
    return get_vibe_remote_dir() / "runtime"


def get_attachments_dir() -> Path:
    return get_vibe_remote_dir() / "attachments"


def get_runtime_pid_path() -> Path:
    return get_runtime_dir() / "vibe.pid"


def get_runtime_ui_pid_path() -> Path:
    return get_runtime_dir() / "vibe-ui.pid"


def get_runtime_remote_access_pid_path() -> Path:
    return get_runtime_dir() / "remote-access-cloudflared.pid"


def get_runtime_status_path() -> Path:
    return get_runtime_dir() / "status.json"


def get_runtime_doctor_path() -> Path:
    return get_runtime_dir() / "doctor.json"


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def get_settings_path() -> Path:
    return get_state_dir() / "settings.json"


def get_sessions_path() -> Path:
    return get_state_dir() / "sessions.json"


def get_watches_path() -> Path:
    return get_state_dir() / "watches.json"


def get_watch_runtime_path() -> Path:
    return get_runtime_dir() / "watch_runtime.json"


def get_discovered_chats_path() -> Path:
    return get_state_dir() / "discovered_chats.json"


def get_user_preferences_path() -> Path:
    return get_state_dir() / "user_preferences.md"


_USER_PREFERENCES_TEMPLATE = """# User Context and Preferences

Use this file for durable user context, stable preferences, and recurring working patterns.
Prefer adding notes under `## Users`.
Keep entries short, factual, reusable, deduplicated, and free of secrets unless the user explicitly asks.

## Users
### platform/user_id
- Add stable notes about how this user prefers to communicate, work, and make decisions.

## Shared
- Add cross-user notes here only when they are genuinely reusable.
"""


def ensure_data_dirs() -> None:
    get_config_dir().mkdir(parents=True, exist_ok=True)
    get_state_dir().mkdir(parents=True, exist_ok=True)
    get_logs_dir().mkdir(parents=True, exist_ok=True)
    get_runtime_dir().mkdir(parents=True, exist_ok=True)
    get_attachments_dir().mkdir(parents=True, exist_ok=True)
    preferences_path = get_user_preferences_path()
    if not preferences_path.exists():
        preferences_path.write_text(_USER_PREFERENCES_TEMPLATE, encoding="utf-8")
