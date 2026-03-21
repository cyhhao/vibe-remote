from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path


PACKAGE_NAME = "vibe-remote"
DEFAULT_UPDATE_METADATA_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
CURRENT_VIBE_EXECUTABLE_ENV = "VIBE_CURRENT_EXECUTABLE"


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


def get_running_vibe_path(
    *,
    vibe_path: str | None = None,
    argv0: str | None = None,
    search_path: str | None = None,
) -> str | None:
    resolved = resolve_command_path(vibe_path, search_path=search_path)
    if resolved:
        return resolved

    env_path = resolve_command_path(os.environ.get(CURRENT_VIBE_EXECUTABLE_ENV), search_path=search_path)
    if env_path:
        return env_path

    argv_path = resolve_command_path(argv0 or sys.argv[0], search_path=search_path)
    if argv_path and Path(argv_path).name.startswith("vibe"):
        return argv_path

    return resolve_command_path("vibe", search_path=search_path)


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


def get_latest_version_info(current_version: str) -> dict:
    result = {"current": current_version, "latest": None, "has_update": False, "error": None}

    try:
        url = get_update_metadata_url()
        req = urllib.request.Request(url, headers={"User-Agent": PACKAGE_NAME})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        latest = data.get("info", {}).get("version", "")
        result["latest"] = latest

        if latest and latest != current_version:
            try:
                current_parts = [int(x) for x in current_version.split(".")[:3] if x.isdigit()]
                latest_parts = [int(x) for x in latest.split(".")[:3] if x.isdigit()]
                result["has_update"] = latest_parts > current_parts
            except (ValueError, AttributeError):
                result["has_update"] = latest != current_version
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
    return str(Path(current_vibe).expanduser().parent)


def build_upgrade_plan(
    *,
    python_executable: str | None = None,
    uv_path: str | None = None,
    vibe_path: str | None = None,
    base_env: dict[str, str] | None = None,
) -> UpgradePlan:
    executable = python_executable or sys.executable
    uv_binary = uv_path if uv_path is not None else shutil.which("uv")
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
