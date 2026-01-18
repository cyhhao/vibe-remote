from pathlib import Path


def get_vibe_remote_dir() -> Path:
    return Path.home() / ".vibe_remote"


def get_config_dir() -> Path:
    return get_vibe_remote_dir() / "config"


def get_state_dir() -> Path:
    return get_vibe_remote_dir() / "state"


def get_logs_dir() -> Path:
    return get_vibe_remote_dir() / "logs"


def get_runtime_dir() -> Path:
    return get_vibe_remote_dir() / "runtime"


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def get_settings_path() -> Path:
    return get_state_dir() / "settings.json"


def get_sessions_path() -> Path:
    return get_state_dir() / "sessions.json"


def ensure_data_dirs() -> None:
    get_config_dir().mkdir(parents=True, exist_ok=True)
    get_state_dir().mkdir(parents=True, exist_ok=True)
    get_logs_dir().mkdir(parents=True, exist_ok=True)
    get_runtime_dir().mkdir(parents=True, exist_ok=True)
