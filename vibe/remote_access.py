"""Runtime helpers for protected remote admin access."""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from config import paths
from config.v2_config import V2Config
from vibe import runtime

CLOUDFLARED_BASE_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download"


def _bin_dir() -> Path:
    return paths.get_vibe_remote_dir() / "bin"


def _managed_cloudflared_path() -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return _bin_dir() / f"cloudflared{suffix}"


def _asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if machine in {"x86_64", "amd64"}:
        arch = "amd64"
    elif machine in {"aarch64", "arm64"}:
        arch = "arm64"
    elif machine in {"i386", "i686", "x86"}:
        arch = "386"
    else:
        raise ValueError(f"Unsupported architecture for cloudflared: {machine}")

    if system == "linux":
        return f"cloudflared-linux-{arch}"
    if system == "darwin":
        if arch not in {"amd64", "arm64"}:
            raise ValueError(f"Unsupported macOS architecture for cloudflared: {machine}")
        return f"cloudflared-darwin-{arch}.tgz"
    if system == "windows":
        if arch != "amd64":
            raise ValueError(f"Unsupported Windows architecture for cloudflared: {machine}")
        return "cloudflared-windows-amd64.exe"
    raise ValueError(f"Unsupported OS for cloudflared: {system}")


def _make_executable(path: Path) -> None:
    if os.name == "nt":
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _version(path: str) -> str | None:
    try:
        result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=8, check=False)
    except Exception:
        return None
    output = (result.stdout or result.stderr or "").strip().splitlines()
    return output[0] if output else None


def _resolve_configured_binary(config: V2Config | None = None) -> str | None:
    if config is not None:
        configured = getattr(config.remote_access.cloudflare, "cloudflared_path", "") or ""
        if configured:
            expanded = Path(configured).expanduser()
            if expanded.exists() and os.access(expanded, os.X_OK):
                return str(expanded)

    managed = _managed_cloudflared_path()
    if managed.exists() and os.access(managed, os.X_OK):
        return str(managed)

    detected = shutil.which("cloudflared")
    return detected


def install_cloudflared() -> dict[str, Any]:
    """Download the official cloudflared binary into the Vibe Remote data dir."""
    paths.ensure_data_dirs()
    _bin_dir().mkdir(parents=True, exist_ok=True)

    asset = _asset_name()
    url = f"{CLOUDFLARED_BASE_URL}/{asset}"
    target = _managed_cloudflared_path()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        download_path = tmp_dir / asset
        urllib.request.urlretrieve(url, download_path)

        if asset.endswith(".tgz"):
            with tarfile.open(download_path, "r:gz") as archive:
                member = next((item for item in archive.getmembers() if Path(item.name).name == "cloudflared"), None)
                if member is None:
                    raise RuntimeError("Downloaded cloudflared archive did not contain a cloudflared binary")
                archive.extract(member, tmp_dir)
                extracted = tmp_dir / member.name
                shutil.move(str(extracted), target)
        else:
            shutil.move(str(download_path), target)

    _make_executable(target)
    return {
        "ok": True,
        "path": str(target),
        "version": _version(str(target)),
        "source_url": url,
    }


def _pid_path() -> Path:
    return paths.get_runtime_remote_access_pid_path()


def _status_from_pid(pid: int | None) -> dict[str, Any]:
    running = bool(pid and runtime.pid_alive(pid))
    command = runtime.get_process_command(pid) if running and pid else None
    return {
        "running": running,
        "pid": pid if running else None,
        "command": command,
    }


def status(config: V2Config | None = None) -> dict[str, Any]:
    try:
        config = config or V2Config.load()
    except Exception:
        config = None

    binary = _resolve_configured_binary(config)
    pid = None
    pid_path = _pid_path()
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except Exception:
            pid = None

    process_status = _status_from_pid(pid)
    if pid and not process_status["running"]:
        pid_path.unlink(missing_ok=True)

    cloudflare = getattr(getattr(config, "remote_access", None), "cloudflare", None) if config is not None else None
    return {
        "ok": True,
        "provider": "cloudflare",
        "enabled": bool(getattr(cloudflare, "enabled", False)),
        "hostname": getattr(cloudflare, "hostname", "") if cloudflare is not None else "",
        "binary_found": bool(binary),
        "binary_path": binary,
        "binary_version": _version(binary) if binary else None,
        **process_status,
    }


def stop_cloudflare() -> dict[str, Any]:
    stopped = runtime.stop_process(_pid_path(), timeout=8)
    return {"ok": True, "stopped": stopped, **status()}


def start_cloudflare(config: V2Config | None = None) -> dict[str, Any]:
    config = config or V2Config.load()
    cloudflare = config.remote_access.cloudflare
    if not cloudflare.enabled:
        return stop_cloudflare()
    if not cloudflare.tunnel_token:
        return {"ok": False, "error": "missing_tunnel_token", **status(config)}

    existing = status(config)
    if existing.get("running"):
        return {"ok": True, "started": False, **existing}

    binary = _resolve_configured_binary(config)
    if not binary:
        return {"ok": False, "error": "cloudflared_not_installed", **existing}

    paths.ensure_data_dirs()
    env = {
        **os.environ,
        "TUNNEL_TOKEN": cloudflare.tunnel_token,
    }
    pid = runtime.spawn_background(
        [binary, "tunnel", "--no-autoupdate", "run"],
        _pid_path(),
        "remote_access_cloudflared_stdout.log",
        "remote_access_cloudflared_stderr.log",
        env=env,
    )
    time.sleep(0.2)
    return {"ok": True, "started": True, "pid": pid, **status(config)}


def reconcile(config: V2Config | None = None) -> dict[str, Any]:
    config = config or V2Config.load()
    if config.remote_access.cloudflare.enabled:
        return start_cloudflare(config)
    return stop_cloudflare()
