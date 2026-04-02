from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast


PACKAGE_NAME = "vibe-remote"
DEFAULT_UPDATE_METADATA_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
CURRENT_VIBE_EXECUTABLE_ENV = "VIBE_CURRENT_EXECUTABLE"
UV_FALLBACK_BIN_DIRS = (".local/bin", ".cargo/bin")
_VERSION_RE = re.compile(
    r"^\s*v?(?P<release>\d+(?:\.\d+)*)"
    r"(?:(?:[.-])?(?P<stage>a|b|rc|dev)(?P<stage_num>\d+))?"
    r"(?:(?:[.-])?post(?P<post_num>\d+))?\s*$"
)
_STAGE_ORDER = {"dev": 0, "a": 1, "b": 2, "rc": 3, "final": 4}


@dataclass(frozen=True)
class UpgradePlan:
    command: list[str]
    env: dict[str, str] | None
    method: str


def resolve_command_path(command: str | None, search_path: str | None = None) -> str | None:
    if not command:
        return None

    expanded = Path(command).expanduser()
    if expanded.is_absolute():
        return os.path.abspath(str(expanded))

    if any(sep in command for sep in (os.sep, "/")):
        return os.path.abspath(str(Path.cwd() / expanded))

    resolved = shutil.which(command, path=search_path)
    if not resolved:
        return None
    return os.path.abspath(os.path.expanduser(resolved))


def is_usable_command_path(path: str | None) -> bool:
    if not path:
        return False
    return os.path.exists(path) and os.access(path, os.X_OK)


def get_launcher_bin_dir(command_path: str) -> str:
    current = os.path.abspath(os.path.expanduser(command_path))

    while os.path.islink(current):
        target = os.readlink(current)
        if not os.path.isabs(target):
            target = os.path.abspath(os.path.join(os.path.dirname(current), target))
        else:
            target = os.path.abspath(os.path.expanduser(target))

        if not os.path.islink(target):
            return str(Path(current).parent)

        current = target

    return str(Path(current).parent)


def get_known_uv_paths(base_env: Mapping[str, str] | None = None) -> list[str]:
    env = base_env or os.environ
    home = env.get("HOME")
    if home is not None:
        return [os.path.join(home, bin_dir, "uv") for bin_dir in UV_FALLBACK_BIN_DIRS]
    return [os.path.expanduser(f"~/{bin_dir}/uv") for bin_dir in UV_FALLBACK_BIN_DIRS]


def find_uv_binary(uv_path: str | None = None, base_env: Mapping[str, str] | None = None) -> str | None:
    env = base_env or os.environ
    search_path = env.get("PATH")

    resolved = resolve_command_path(uv_path, search_path=search_path)
    if is_usable_command_path(resolved):
        return resolved

    resolved = resolve_command_path("uv", search_path=search_path)
    if is_usable_command_path(resolved):
        return resolved

    for candidate in get_known_uv_paths(base_env=env):
        resolved = resolve_command_path(candidate, search_path=search_path)
        if is_usable_command_path(resolved):
            return resolved

    return None


def get_running_vibe_path(
    *,
    vibe_path: str | None = None,
    argv0: str | None = None,
    search_path: str | None = None,
) -> str | None:
    resolved = resolve_command_path(vibe_path, search_path=search_path)
    if is_usable_command_path(resolved):
        return resolved

    env_path = resolve_command_path(os.environ.get(CURRENT_VIBE_EXECUTABLE_ENV), search_path=search_path)
    if is_usable_command_path(env_path):
        return env_path

    argv_path = resolve_command_path(argv0 or sys.argv[0], search_path=search_path)
    if is_usable_command_path(argv_path):
        argv_path_str = cast(str, argv_path)
        if Path(argv_path_str).name.startswith("vibe"):
            return argv_path_str

    fallback_path = resolve_command_path("vibe", search_path=search_path)
    if is_usable_command_path(fallback_path):
        return fallback_path
    return None


def cache_running_vibe_path(vibe_path: str | None = None) -> str | None:
    resolved = get_running_vibe_path(vibe_path=vibe_path)
    if resolved:
        os.environ[CURRENT_VIBE_EXECUTABLE_ENV] = resolved
    return resolved


def get_restart_command(
    *,
    vibe_path: str | None = None,
    python_executable: str | None = None,
    argv0: str | None = None,
    search_path: str | None = None,
) -> list[str]:
    resolved = get_running_vibe_path(vibe_path=vibe_path, argv0=argv0, search_path=search_path)
    if resolved:
        return [resolved]
    return [python_executable or sys.executable, "-c", "from vibe.cli import main; main()"]


def get_restart_shell_command(
    *,
    vibe_path: str | None = None,
    python_executable: str | None = None,
    argv0: str | None = None,
    search_path: str | None = None,
) -> str:
    return shlex.join(
        get_restart_command(
            vibe_path=vibe_path,
            python_executable=python_executable,
            argv0=argv0,
            search_path=search_path,
        )
    )


def get_update_metadata_url() -> str:
    return os.environ.get("VIBE_UPDATE_METADATA_URL", DEFAULT_UPDATE_METADATA_URL)


def get_upgrade_package_spec() -> str:
    return os.environ.get("VIBE_UPGRADE_PACKAGE_SPEC", PACKAGE_NAME)


def _normalize_release_parts(parts: tuple[int, ...]) -> tuple[int, ...]:
    normalized = list(parts)
    while len(normalized) > 1 and normalized[-1] == 0:
        normalized.pop()
    return tuple(normalized)


def _parse_version(value: str) -> tuple[tuple[int, ...], int, int, int, int] | None:
    match = _VERSION_RE.match(value)
    if not match:
        return None

    release = tuple(int(part) for part in match.group("release").split("."))
    stage = match.group("stage") or "final"
    stage_num = int(match.group("stage_num") or "0")
    post_num = int(match.group("post_num") or "0")
    has_post = 1 if match.group("post_num") else 0
    return (_normalize_release_parts(release), _STAGE_ORDER[stage], stage_num, has_post, post_num)


def _is_prerelease_version(value: str) -> bool:
    parsed = _parse_version(value)
    if parsed is None:
        return False
    return parsed[1] < _STAGE_ORDER["final"]


def _is_yanked_release(files: object) -> bool:
    if not isinstance(files, list) or not files:
        return False
    yanked_flags = [bool(item.get("yanked")) for item in files if isinstance(item, dict)]
    return bool(yanked_flags) and all(yanked_flags)


def select_latest_update_version(metadata: Mapping[str, object], current_version: str) -> str:
    allow_prereleases = _is_prerelease_version(current_version)
    releases = metadata.get("releases")

    candidates: list[tuple[object, str]] = []
    if isinstance(releases, Mapping):
        for version_str, files in releases.items():
            if not isinstance(version_str, str):
                continue
            parsed = _parse_version(version_str)
            if parsed is None:
                continue
            if not allow_prereleases and _is_prerelease_version(version_str):
                continue
            if _is_yanked_release(files):
                continue
            candidates.append((parsed, version_str))

    if candidates:
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][1]

    latest = str((metadata.get("info") or {}).get("version") or "")
    if latest and (allow_prereleases or not _is_prerelease_version(latest)):
        return latest
    return ""


def has_newer_version(candidate: str, current: str) -> bool:
    if not candidate or candidate == current:
        return False

    latest_parsed = _parse_version(candidate)
    current_parsed = _parse_version(current)
    if latest_parsed is not None and current_parsed is not None:
        return latest_parsed > current_parsed

    try:
        current_parts = [int(x) for x in current.split(".")[:3] if x.isdigit()]
        latest_parts = [int(x) for x in candidate.split(".")[:3] if x.isdigit()]
        return latest_parts > current_parts
    except (ValueError, AttributeError):
        return candidate != current


def get_latest_version_info(current_version: str) -> dict:
    result = {"current": current_version, "latest": None, "has_update": False, "error": None}

    try:
        url = get_update_metadata_url()
        req = urllib.request.Request(url, headers={"User-Agent": PACKAGE_NAME})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        latest = select_latest_update_version(data, current_version)
        result["latest"] = latest

        if latest and latest != current_version:
            result["has_update"] = has_newer_version(latest, current_version)
    except Exception as e:
        result["error"] = str(e)

    return result


def is_uv_tool_install(python_executable: str | None = None) -> bool:
    executable = (python_executable or sys.executable or "").replace("\\", "/")
    return "/uv/tools/" in executable


def get_current_vibe_bin_dir(vibe_path: str | None = None) -> str | None:
    current_vibe = get_running_vibe_path(vibe_path=vibe_path)
    if not current_vibe:
        return None

    return get_launcher_bin_dir(current_vibe)


def build_upgrade_plan(
    *,
    python_executable: str | None = None,
    uv_path: str | None = None,
    vibe_path: str | None = None,
    base_env: dict[str, str] | None = None,
) -> UpgradePlan:
    executable = python_executable or sys.executable
    uv_binary = find_uv_binary(uv_path=uv_path, base_env=base_env)
    package_spec = get_upgrade_package_spec()

    if is_uv_tool_install(executable) and uv_binary:
        env = dict(base_env or os.environ)
        vibe_bin_dir = get_current_vibe_bin_dir(vibe_path)
        if vibe_bin_dir:
            env["UV_TOOL_BIN_DIR"] = vibe_bin_dir
        command = [uv_binary, "tool", "install", package_spec, "--upgrade"]
        if package_spec != PACKAGE_NAME:
            command.append("--force")
        return UpgradePlan(
            command=command,
            env=env,
            method="uv",
        )

    return UpgradePlan(
        command=[executable, "-m", "pip", "install", "--upgrade", package_spec],
        env=dict(base_env or os.environ),
        method="pip",
    )


def get_safe_cwd() -> str:
    """Return a stable, existing absolute directory for subprocess cwd.

    The vibe service process cwd may be inside the uv tool venv directory,
    which uv deletes and recreates during upgrade.  Using the home directory
    avoids 'Current directory does not exist' errors.  Falls back to the
    system temp directory or ``/`` when HOME is unset or invalid.
    """
    for candidate in (os.path.expanduser("~"), tempfile.gettempdir(), "/"):
        if os.path.isabs(candidate) and os.path.isdir(candidate):
            return candidate
    return "/"
