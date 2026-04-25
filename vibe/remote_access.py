"""Vibe Cloud remote-access runtime and auth helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import ntpath
import os
import platform
import shlex
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import jwt
from jwt import PyJWKClient

from config import paths
from config.v2_config import V2Config
from vibe import api, runtime

CLOUDFLARED_BASE_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download"
SESSION_COOKIE_NAME = "__Host-vibe_remote_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
_CONNECTOR_LOCK = threading.RLock()


def _bin_dir() -> Path:
    return paths.get_vibe_remote_dir() / "bin"


def _managed_cloudflared_path() -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return _bin_dir() / f"cloudflared{suffix}"


def _pid_path() -> Path:
    return paths.get_runtime_remote_access_pid_path()


def _state_path() -> Path:
    return paths.get_runtime_dir() / "remote-access-cloudflared.json"


def _asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "amd64" if machine in {"x86_64", "amd64"} else "arm64" if machine in {"aarch64", "arm64"} else ""
    if not arch:
        raise ValueError(f"Unsupported architecture for cloudflared: {machine}")
    if system == "linux":
        return f"cloudflared-linux-{arch}"
    if system == "darwin":
        return f"cloudflared-darwin-{arch}.tgz"
    if system == "windows" and arch == "amd64":
        return "cloudflared-windows-amd64.exe"
    raise ValueError(f"Unsupported OS for cloudflared: {system}")


def _version(path: str) -> str | None:
    try:
        result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=8, check=False)
    except Exception:
        return None
    output = (result.stdout or result.stderr or "").strip().splitlines()
    return output[0] if output else None


def _make_executable(path: Path) -> None:
    if os.name == "nt":
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_stream_atomically(source: Any, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_name(f".{target.name}.tmp")
    temp_target.unlink(missing_ok=True)
    try:
        with temp_target.open("wb") as output:
            shutil.copyfileobj(source, output)
        os.replace(temp_target, target)
    except Exception:
        temp_target.unlink(missing_ok=True)
        raise


def _safe_extract_cloudflared(archive: tarfile.TarFile, target: Path) -> None:
    for member in archive.getmembers():
        member_name = Path(member.name)
        if member_name.name != "cloudflared":
            continue
        if member_name.is_absolute() or ".." in member_name.parts or not member.isfile():
            raise RuntimeError("Downloaded cloudflared archive contained an unsafe entry")
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError("Downloaded cloudflared archive did not contain a readable binary")
        _copy_stream_atomically(source, target)
        return
    raise RuntimeError("Downloaded cloudflared archive did not contain cloudflared")


def install_cloudflared() -> dict[str, Any]:
    try:
        paths.ensure_data_dirs()
        asset = _asset_name()
        url = f"{CLOUDFLARED_BASE_URL}/{asset}"
        target = _managed_cloudflared_path()
        with tempfile.TemporaryDirectory() as tmp:
            download_path = Path(tmp) / asset
            urllib.request.urlretrieve(url, download_path)
            if asset.endswith(".tgz"):
                with tarfile.open(download_path, "r:gz") as archive:
                    _safe_extract_cloudflared(archive, target)
            else:
                with download_path.open("rb") as source:
                    _copy_stream_atomically(source, target)
        _make_executable(target)
        return {"ok": True, "path": str(target), "version": _version(str(target)), "source_url": url}
    except Exception as exc:
        return {"ok": False, "error": "cloudflared_install_failed", "detail": str(exc)}


def _resolve_binary(config: V2Config | None = None) -> str | None:
    configured = ""
    if config is not None:
        configured = getattr(config.remote_access.vibe_cloud, "cloudflared_path", "") or ""
    if configured:
        expanded = Path(configured).expanduser()
        if expanded.exists() and os.access(expanded, os.X_OK):
            return str(expanded)
    managed = _managed_cloudflared_path()
    if managed.exists() and os.access(managed, os.X_OK):
        return str(managed)
    return shutil.which("cloudflared")


def _read_pid() -> int | None:
    try:
        return int(_pid_path().read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _is_cloudflared_pid(pid: int | None) -> bool:
    if not pid or not runtime.pid_alive(pid):
        return False
    command = runtime.get_process_command(pid) or ""
    try:
        parts = shlex.split(command.strip(), posix=False)
    except ValueError:
        parts = command.strip().split()
    executable = parts[0].strip("\"'") if parts else ""
    executable_name = Path(executable).name.lower()
    windows_name = ntpath.basename(executable).lower()
    return executable_name in {"cloudflared", "cloudflared.exe"} or windows_name in {"cloudflared", "cloudflared.exe"}


def _write_state(pid: int, config: V2Config, binary: str) -> None:
    _state_path().write_text(json.dumps({"pid": pid, **_runtime_signature(config, binary)}, indent=2), encoding="utf-8")


def _runtime_signature(config: V2Config, binary: str) -> dict[str, str]:
    cloud = config.remote_access.vibe_cloud
    return {
        "provider": "vibe_cloud",
        "binary_path": binary,
        "public_url": cloud.public_url,
        "tunnel_token_sha256": hashlib.sha256((cloud.tunnel_token or "").encode("utf-8")).hexdigest(),
    }


def _read_state() -> dict[str, Any] | None:
    try:
        payload = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _running_signature(pid: int | None) -> dict[str, str] | None:
    if not _is_cloudflared_pid(pid):
        return None
    state = _read_state()
    if state is None or state.get("pid") != pid:
        return None
    return {
        "provider": str(state.get("provider") or ""),
        "binary_path": str(state.get("binary_path") or ""),
        "public_url": str(state.get("public_url") or ""),
        "tunnel_token_sha256": str(state.get("tunnel_token_sha256") or ""),
    }


def status(config: V2Config | None = None) -> dict[str, Any]:
    try:
        config = config or V2Config.load()
    except Exception:
        config = None
    pid = _read_pid()
    running = _is_cloudflared_pid(pid)
    if pid and not running:
        _pid_path().unlink(missing_ok=True)
        _state_path().unlink(missing_ok=True)
    cloud = getattr(getattr(config, "remote_access", None), "vibe_cloud", None) if config else None
    binary = _resolve_binary(config)
    return {
        "ok": True,
        "provider": "vibe_cloud",
        "enabled": bool(getattr(cloud, "enabled", False)),
        "public_url": getattr(cloud, "public_url", "") if cloud else "",
        "paired": bool(getattr(cloud, "instance_id", "") and getattr(cloud, "tunnel_token", "")) if cloud else False,
        "running": running,
        "pid": pid if running else None,
        "binary_found": bool(binary),
        "binary_path": binary,
        "binary_version": _version(binary) if binary else None,
    }


def stop() -> dict[str, Any]:
    with _CONNECTOR_LOCK:
        pid = _read_pid()
        if pid is not None and not _is_cloudflared_pid(pid):
            _pid_path().unlink(missing_ok=True)
            _state_path().unlink(missing_ok=True)
            return {**status(), "ok": True, "stopped": False, "stale_pid": True}
        stopped = runtime.stop_pid(pid, timeout=8) if pid is not None else False
        if stopped:
            _pid_path().unlink(missing_ok=True)
            _state_path().unlink(missing_ok=True)
        if pid is not None and not stopped and _is_cloudflared_pid(pid):
            return {**status(), "ok": False, "error": "cloudflared_stop_failed", "stopped": False}
        return {**status(), "ok": True, "stopped": stopped}


def rotate_session_secret(config: V2Config) -> None:
    config.remote_access.vibe_cloud.session_secret = secrets.token_urlsafe(32)
    config.save()


def start(config: V2Config | None = None) -> dict[str, Any]:
    with _CONNECTOR_LOCK:
        config = config or V2Config.load()
        cloud = config.remote_access.vibe_cloud
        if not cloud.enabled:
            stop_result = stop()
            return {**stop_result, "ok": False, "error": "remote_access_disabled"}
        if not cloud.tunnel_token:
            stop_result = stop()
            return {**stop_result, "ok": False, "error": "missing_tunnel_token"}
        binary = _resolve_binary(config)
        if not binary:
            install_result = install_cloudflared()
            if install_result.get("ok") is False:
                return {**status(config), **install_result}
            binary = str(install_result["path"])
        current = status(config)
        if current.get("running"):
            running_sig = _running_signature(current.get("pid"))
            desired_sig = _runtime_signature(config, binary)
            if running_sig == desired_sig:
                return {**current, "ok": True, "started": False}
            stop_result = stop()
            if stop_result.get("ok") is False or stop_result.get("running"):
                return {
                    **stop_result,
                    "ok": False,
                    "error": stop_result.get("error") or "cloudflared_stop_failed",
                    "restarted": False,
                }
        env = {**os.environ, "TUNNEL_TOKEN": cloud.tunnel_token}
        try:
            pid = runtime.spawn_background(
                [binary, "tunnel", "--no-autoupdate", "run"],
                _pid_path(),
                "remote_access_cloudflared_stdout.log",
                "remote_access_cloudflared_stderr.log",
                env=env,
            )
            _write_state(pid, config, binary)
        except Exception as exc:
            return {**status(config), "ok": False, "error": "cloudflared_spawn_failed", "detail": str(exc)}
        time.sleep(0.2)
        current = status(config)
        if not current.get("running"):
            return {**current, "ok": False, "error": "cloudflared_exited"}
        return {**current, "ok": True, "started": True, "pid": pid}


def reconcile(config: V2Config | None = None) -> dict[str, Any]:
    config = config or V2Config.load()
    if config.remote_access.provider == "vibe_cloud" and config.remote_access.vibe_cloud.enabled:
        return start(config)
    return stop()


def _json_request(url: str, payload: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def pair(pairing_key: str, backend_url: str, device_name: str = "Vibe Remote") -> dict[str, Any]:
    pairing_key = (pairing_key or "").strip()
    backend_url = (backend_url or "https://vibe.io").strip().rstrip("/")
    if not pairing_key:
        return {"ok": False, "error": "missing_pairing_key"}
    try:
        result = _json_request(
            f"{backend_url}/api/v1/pairing/redeem",
            {"pairing_key": pairing_key, "device_name": device_name, "local_version": "dev"},
        )
    except Exception as exc:
        return {"ok": False, "error": "pairing_request_failed", "detail": str(exc)}
    required = ("instance_id", "client_id", "issuer", "authorization_endpoint", "token_endpoint", "jwks_uri", "public_url", "redirect_uri", "tunnel_token", "instance_secret")
    missing = [field for field in required if not result.get(field)]
    if missing:
        return {"ok": False, "error": "invalid_pairing_response", "missing": missing}
    config = api.load_config()
    cloud = config.remote_access.vibe_cloud
    cloud.enabled = True
    cloud.backend_url = backend_url
    cloud.instance_id = result["instance_id"]
    cloud.client_id = result["client_id"]
    cloud.issuer = result["issuer"]
    cloud.authorization_endpoint = result["authorization_endpoint"]
    cloud.token_endpoint = result["token_endpoint"]
    cloud.jwks_uri = result["jwks_uri"]
    cloud.public_url = result["public_url"]
    cloud.redirect_uri = result["redirect_uri"]
    cloud.tunnel_token = result["tunnel_token"]
    cloud.instance_secret = result["instance_secret"]
    cloud.session_secret = cloud.session_secret or secrets.token_urlsafe(32)
    config.save()
    start_result = start(config)
    return {**status(config), "ok": True, "pairing": {"ok": True}, "start": start_result}


def _session_signature(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_session_cookie(config: V2Config, email: str, subject: str) -> str:
    cloud = config.remote_access.vibe_cloud
    issued_at = int(time.time())
    payload = {
        "email": email,
        "sub": subject,
        "instance_id": cloud.instance_id,
        "iat": issued_at,
        "exp": issued_at + SESSION_TTL_SECONDS,
    }
    payload_text = urllib.parse.quote(json.dumps(payload, separators=(",", ":")), safe="")
    signature = _session_signature(cloud.session_secret, payload_text)
    return f"{payload_text}.{signature}"


def validate_session_cookie(config: V2Config, cookie_value: str | None) -> bool:
    if not cookie_value or "." not in cookie_value:
        return False
    cloud = config.remote_access.vibe_cloud
    payload_text, signature = cookie_value.rsplit(".", 1)
    expected = _session_signature(cloud.session_secret, payload_text)
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        payload = json.loads(urllib.parse.unquote(payload_text))
    except Exception:
        return False
    return payload.get("instance_id") == cloud.instance_id and int(payload.get("exp", 0)) > int(time.time())


def authorization_url(config: V2Config, state: str, nonce: str, code_challenge: str) -> str:
    cloud = config.remote_access.vibe_cloud
    params = {
        "client_id": cloud.client_id,
        "redirect_uri": cloud.redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if cloud.dev_login_hint:
        params["login_hint"] = cloud.dev_login_hint
    return f"{cloud.authorization_endpoint}?{urllib.parse.urlencode(params)}"


def exchange_oauth_code(config: V2Config, code: str, code_verifier: str) -> dict[str, Any]:
    cloud = config.remote_access.vibe_cloud
    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": cloud.client_id,
            "redirect_uri": cloud.redirect_uri,
            "code": code,
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        cloud.token_endpoint,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        token_payload = json.loads(response.read().decode("utf-8"))
    id_token = token_payload.get("id_token")
    if not id_token:
        raise ValueError("missing_id_token")
    jwk_client = PyJWKClient(cloud.jwks_uri)
    signing_key = jwk_client.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(id_token, signing_key.key, algorithms=["RS256"], audience=cloud.client_id, issuer=cloud.issuer)
    if claims.get("vibe_instance_id") != cloud.instance_id:
        raise ValueError("invalid_instance_id")
    if not claims.get("email_verified"):
        raise ValueError("email_not_verified")
    return {"claims": claims, "token": token_payload}
