"""Runtime helpers for protected remote admin access."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import platform
import shlex
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from config import paths
from config.v2_config import V2Config
from vibe import runtime

CLOUDFLARED_BASE_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download"
_CONNECTOR_ENV: dict[int, dict[str, str]] = {}
_CONNECTOR_LOCK = threading.RLock()


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


def _safe_extract_cloudflared(archive: tarfile.TarFile, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_name(f".{target.name}.tmp")
    temp_target.unlink(missing_ok=True)
    for member in archive.getmembers():
        member_name = Path(member.name)
        if member_name.name != "cloudflared":
            continue
        if member_name.is_absolute() or ".." in member_name.parts:
            raise RuntimeError("Downloaded cloudflared archive contained an unsafe cloudflared path")
        if not member.isfile():
            raise RuntimeError("Downloaded cloudflared archive did not contain a regular cloudflared binary")
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError("Downloaded cloudflared archive did not contain a readable cloudflared binary")
        try:
            _copy_stream_atomically(source, target, temp_target)
        except Exception:
            temp_target.unlink(missing_ok=True)
            raise
        return
    raise RuntimeError("Downloaded cloudflared archive did not contain a cloudflared binary")


def _copy_stream_atomically(source: Any, target: Path, temp_target: Path | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = temp_target or target.with_name(f".{target.name}.tmp")
    temp_target.unlink(missing_ok=True)
    try:
        with temp_target.open("wb") as output:
            shutil.copyfileobj(source, output)
        os.replace(temp_target, target)
    except Exception:
        temp_target.unlink(missing_ok=True)
        raise


def _copy_file_atomically(source: Path, target: Path) -> None:
    with source.open("rb") as source_file:
        _copy_stream_atomically(source_file, target)


def install_cloudflared() -> dict[str, Any]:
    """Download the official cloudflared binary into the Vibe Remote data dir."""
    try:
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
                    _safe_extract_cloudflared(archive, target)
            else:
                _copy_file_atomically(download_path, target)

        _make_executable(target)
        return {
            "ok": True,
            "path": str(target),
            "version": _version(str(target)),
            "source_url": url,
        }
    except Exception as exc:
        return {"ok": False, "error": "cloudflared_install_failed", "detail": str(exc)}


def _pid_path() -> Path:
    return paths.get_runtime_remote_access_pid_path()


def _state_path() -> Path:
    return paths.get_runtime_dir() / "remote-access-cloudflared.json"


def _status_from_pid(pid: int | None, expected_binary: str | None = None) -> dict[str, Any]:
    running = bool(pid and _is_cloudflared_pid(pid, expected_binary=expected_binary))
    command = runtime.get_process_command(pid) if running and pid else None
    return {
        "running": running,
        "pid": pid if running else None,
        "command": command,
    }


def _desired_runtime_signature(config: V2Config, binary: str) -> dict[str, str]:
    cloudflare = config.remote_access.cloudflare
    return {
        "binary_path": str(binary),
        "tunnel_token_sha256": hashlib.sha256((cloudflare.tunnel_token or "").encode("utf-8")).hexdigest(),
    }


def _cloudflare_access_ready(cloudflare: Any) -> bool:
    allowed_emails = [value for value in (getattr(cloudflare, "allowed_emails", None) or []) if str(value).strip()]
    allowed_domains = [
        value for value in (getattr(cloudflare, "allowed_email_domains", None) or []) if str(value).strip()
    ]
    return bool(
        getattr(cloudflare, "hostname", "")
        and getattr(cloudflare, "confirmed_access_policy", False)
        and getattr(cloudflare, "confirmed_tunnel_route", False)
        and (allowed_emails or allowed_domains)
    )


def _write_running_state(pid: int, signature: dict[str, str]) -> None:
    paths.ensure_data_dirs()
    _state_path().write_text(json.dumps({"pid": pid, **signature}, indent=2), encoding="utf-8")


def _read_running_state() -> dict[str, Any] | None:
    try:
        payload = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _command_executable(command: str | None) -> str | None:
    if not command or not command.strip():
        return None
    try:
        parts = shlex.split(command.strip(), posix=False)
    except ValueError:
        parts = command.strip().split()
    if not parts:
        return None
    return parts[0].strip("\"'")


def _same_executable_path(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    normalized_left = left.strip("\"'")
    normalized_right = right.strip("\"'")
    return (
        os.path.normcase(os.path.normpath(normalized_left))
        == os.path.normcase(os.path.normpath(normalized_right))
        or ntpath.normcase(ntpath.normpath(normalized_left))
        == ntpath.normcase(ntpath.normpath(normalized_right))
    )


def _has_command_boundary(command: str, executable: str) -> bool:
    if len(command) == len(executable):
        return True
    return len(command) > len(executable) and command[len(executable)].isspace()


def _command_starts_with_expected_binary(command: str | None, expected_binary: str | None) -> bool:
    if not command or not expected_binary:
        return False

    command_text = command.strip()
    expected = expected_binary.strip("\"'")
    if not command_text or not expected:
        return False

    for quote in ('"', "'"):
        quoted = f"{quote}{expected}{quote}"
        if command_text.startswith(quoted) and _has_command_boundary(command_text, quoted):
            return True

    candidates = {
        expected,
        os.path.normpath(expected),
        ntpath.normpath(expected),
    }
    command_variants = {
        command_text,
        os.path.normcase(command_text),
        ntpath.normcase(command_text),
    }
    for candidate in candidates:
        candidate_variants = {
            candidate,
            os.path.normcase(candidate),
            ntpath.normcase(candidate),
        }
        for command_variant in command_variants:
            for candidate_variant in candidate_variants:
                if command_variant.startswith(candidate_variant) and _has_command_boundary(
                    command_variant, candidate_variant
                ):
                    return True
    return False


def _runtime_binary_for_pid(pid: int) -> str | None:
    signature = _CONNECTOR_ENV.get(pid)
    if signature is not None:
        return signature.get("binary_path")
    state = _read_running_state()
    if state is not None and state.get("pid") == pid:
        return str(state.get("binary_path") or "")
    return None


def _is_cloudflared_command(command: str | None, expected_binary: str | None = None) -> bool:
    if _command_starts_with_expected_binary(command, expected_binary):
        return True

    executable = _command_executable(command)
    if not executable:
        return False
    if _same_executable_path(executable, expected_binary):
        return True
    if expected_binary:
        return False
    executable_name = ntpath.basename(executable).lower()
    return executable_name in {"cloudflared", "cloudflared.exe"}


def _is_cloudflared_pid(pid: int | None, expected_binary: str | None = None) -> bool:
    if not pid or not runtime.pid_alive(pid):
        return False
    return _is_cloudflared_command(runtime.get_process_command(pid), _runtime_binary_for_pid(pid) or expected_binary)


def _clear_running_state(pid: int | None = None) -> None:
    state = _read_running_state()
    if pid is not None and state is not None and state.get("pid") != pid:
        return
    _state_path().unlink(missing_ok=True)


def _running_signature(pid: int | None) -> dict[str, str] | None:
    if not _is_cloudflared_pid(pid):
        return None
    signature = _CONNECTOR_ENV.get(pid)
    if signature is not None:
        return signature
    state = _read_running_state()
    if state is None or state.get("pid") != pid:
        return None
    return {
        "binary_path": str(state.get("binary_path") or ""),
        "tunnel_token_sha256": str(state.get("tunnel_token_sha256") or ""),
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

    process_status = _status_from_pid(pid, expected_binary=binary)
    if pid and not process_status["running"]:
        pid_path.unlink(missing_ok=True)
        _CONNECTOR_ENV.pop(pid, None)
        _clear_running_state(pid)

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
    with _CONNECTOR_LOCK:
        pid = None
        pid_path = _pid_path()
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
            except Exception:
                pid = None
        if pid_path.exists() and pid is None:
            pid_path.unlink(missing_ok=True)
            return {**status(), "ok": True, "stopped": False, "stale_pid": True}
        if pid is not None and not _is_cloudflared_pid(pid):
            pid_path.unlink(missing_ok=True)
            _CONNECTOR_ENV.pop(pid, None)
            _clear_running_state(pid)
            return {**status(), "ok": True, "stopped": False, "stale_pid": True}

        stopped = runtime.stop_process(_pid_path(), timeout=8)
        if pid is not None and stopped:
            _CONNECTOR_ENV.pop(pid, None)
            _clear_running_state(pid)
        if pid is not None and not stopped and _is_cloudflared_pid(pid):
            current = status()
            return {**current, "ok": False, "error": "cloudflared_stop_failed", "stopped": False}
        return {**status(), "ok": True, "stopped": stopped}


def _stop_before_start_failure(config: V2Config, error: str) -> dict[str, Any]:
    stop_result = stop_cloudflare()
    if stop_result.get("ok") is False:
        return stop_result
    return {**status(config), "ok": False, "error": error}


def start_cloudflare(config: V2Config | None = None) -> dict[str, Any]:
    with _CONNECTOR_LOCK:
        config = config or V2Config.load()
        cloudflare = config.remote_access.cloudflare
        if not cloudflare.enabled:
            return stop_cloudflare()
        if not cloudflare.tunnel_token:
            return _stop_before_start_failure(config, "missing_tunnel_token")
        if not _cloudflare_access_ready(cloudflare):
            return _stop_before_start_failure(config, "access_checklist_incomplete")

        binary = _resolve_configured_binary(config)
        if not binary:
            return _stop_before_start_failure(config, "cloudflared_not_installed")

        existing = status(config)
        if existing.get("running"):
            running_sig = _running_signature(existing.get("pid"))
            desired_sig = _desired_runtime_signature(config, binary)
            if running_sig != desired_sig:
                stop_result = stop_cloudflare()
                if stop_result.get("ok") is False or stop_result.get("running"):
                    return {
                        **stop_result,
                        "ok": False,
                        "error": stop_result.get("error") or "cloudflared_stop_failed",
                        "restarted": False,
                    }
                started = start_cloudflare(config)
                return {**started, "restarted": True, "stopped": stop_result.get("stopped", False)}
            return {**existing, "ok": True, "started": False}

        paths.ensure_data_dirs()
        env = {
            **os.environ,
            "TUNNEL_TOKEN": cloudflare.tunnel_token,
        }
        try:
            pid = runtime.spawn_background(
                [binary, "tunnel", "--no-autoupdate", "run"],
                _pid_path(),
                "remote_access_cloudflared_stdout.log",
                "remote_access_cloudflared_stderr.log",
                env=env,
            )
        except Exception as exc:
            return {**status(config), "ok": False, "error": "cloudflared_spawn_failed", "detail": str(exc)}
        signature = _desired_runtime_signature(config, binary)
        try:
            _CONNECTOR_ENV[pid] = signature
            _write_running_state(pid, signature)
        except Exception as exc:
            _CONNECTOR_ENV.pop(pid, None)
            runtime.stop_pid(pid, timeout=8)
            _pid_path().unlink(missing_ok=True)
            _clear_running_state(pid)
            return {**status(config), "ok": False, "error": "cloudflared_state_write_failed", "detail": str(exc)}
        time.sleep(0.2)
        current = status(config)
        if not current.get("running"):
            _CONNECTOR_ENV.pop(pid, None)
            _clear_running_state(pid)
            return {**current, "ok": False, "error": "cloudflared_exited"}
        return {**current, "ok": True, "started": True, "pid": pid}


def reconcile(config: V2Config | None = None) -> dict[str, Any]:
    with _CONNECTOR_LOCK:
        config = config or V2Config.load()
        if config.remote_access.cloudflare.enabled:
            return start_cloudflare(config)
        return stop_cloudflare()
