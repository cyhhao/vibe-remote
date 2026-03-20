from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path


PACKAGE_NAME = "vibe-remote"
DEFAULT_UPDATE_METADATA_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"


@dataclass(frozen=True)
class UpgradePlan:
    command: list[str]
    env: dict[str, str] | None
    method: str


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
    current_vibe = vibe_path if vibe_path is not None else shutil.which("vibe")
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
        return UpgradePlan(
            command=[uv_binary, "tool", "install", package_spec, "--upgrade"],
            env=env,
            method="uv",
        )

    return UpgradePlan(
        command=[executable, "-m", "pip", "install", "--upgrade", package_spec],
        env=dict(base_env or os.environ),
        method="pip",
    )
