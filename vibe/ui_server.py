import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit

from flask import Flask, request, jsonify, send_file, Response

from config import paths
from config.v2_config import V2Config
from vibe.runtime import get_ui_dist_path, get_working_dir
from vibe.sentry_integration import init_sentry

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=None)

# Global server instance for graceful shutdown on reload
_server = None

# Disable Flask's default logging
log = logging.getLogger("werkzeug")
log.setLevel(logging.WARNING)

STRUCTURED_LOG_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+-\s+([\w.]+)\s+-\s+(\w+)\s+-\s+(.*)$")
LEVEL_HINT_PATTERN = re.compile(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")
TRACEBACK_EXCEPTION_PATTERN = re.compile(
    r"^[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt|Failure|Fault|Group)(?:[:(]|$)"
)
CSRF_COOKIE_NAME = "vibe_csrf_token"
CSRF_HEADER_NAME = "X-Vibe-CSRF-Token"
REMOTE_OAUTH_COOKIE_NAME = "__Host-vibe_remote_oauth"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOG_SOURCES = (
    ("service", "vibe_remote.log", lambda: paths.get_logs_dir() / "vibe_remote.log"),
    ("service_stdout", "service_stdout.log", lambda: paths.get_runtime_dir() / "service_stdout.log"),
    ("service_stderr", "service_stderr.log", lambda: paths.get_runtime_dir() / "service_stderr.log"),
    ("ui_stdout", "ui_stdout.log", lambda: paths.get_runtime_dir() / "ui_stdout.log"),
    ("ui_stderr", "ui_stderr.log", lambda: paths.get_runtime_dir() / "ui_stderr.log"),
)


def _run_async(coro, timeout: float = 10.0) -> dict:
    """Run async coroutine in a separate thread with timeout."""
    result: dict[str, Any] = {}
    error: str | None = None
    lock = threading.Event()

    def _runner():
        nonlocal result, error
        try:
            result = asyncio.run(coro)
        except Exception as exc:
            error = str(exc)
        finally:
            lock.set()

    threading.Thread(target=_runner, daemon=True).start()
    lock.wait(timeout=timeout)
    if not lock.is_set():
        return {"ok": False, "error": "Request timed out"}
    if error:
        return {"ok": False, "error": error}
    return result


def _is_continuation_line(line: str, previous_message: str | None = None) -> bool:
    stripped = line.lstrip()
    return (
        line[:1].isspace()
        or stripped.startswith("Traceback ")
        or stripped.startswith("During handling of the above exception")
        or stripped.startswith("File ")
        or stripped.startswith("task:")
        or stripped.startswith("^")
        or (
            previous_message is not None
            and "Traceback " in previous_message
            and bool(TRACEBACK_EXCEPTION_PATTERN.match(stripped))
        )
    )


def _fallback_log_entry(line: str, source_key: str) -> dict[str, str]:
    level_match = LEVEL_HINT_PATTERN.search(line)
    level = level_match.group(1) if level_match else "INFO"
    if level == "CRITICAL":
        level = "ERROR"
    return {
        "timestamp": "",
        "logger": source_key,
        "level": level,
        "message": line,
        "source": source_key,
    }


def _timestamp_to_sort_ns(timestamp: str) -> int | None:
    try:
        return int(datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f").timestamp() * 1_000_000_000)
    except ValueError:
        return None


def _serialize_log_entries(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "timestamp": str(entry.get("timestamp", "")),
            "logger": str(entry.get("logger", "")),
            "level": str(entry.get("level", "INFO")),
            "message": str(entry.get("message", "")),
            "source": str(entry.get("source", "")),
        }
        for entry in entries
    ]


def _new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _request_origin(value: str | None) -> str | None:
    if not value:
        return None

    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _current_origin() -> str:
    parsed = urlparse(request.host_url)
    scheme = parsed.scheme
    netloc = parsed.netloc

    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
    forwarded_host = request.headers.get("X-Forwarded-Host", "").split(",")[0].strip()

    if forwarded_proto:
        scheme = forwarded_proto
    if forwarded_host:
        netloc = forwarded_host

    return f"{scheme}://{netloc}"


def _is_mutation_guard_exempt() -> bool:
    if request.path in {"/auth/callback"}:
        return True
    return (
        request.path == "/e2e/simulate-interaction"
        and os.environ.get("E2E_TEST_MODE", "").lower() in ("true", "1", "yes")
    )


def _ensure_csrf_cookie(response: Response) -> Response:
    if response.headers.getlist("Set-Cookie"):
        for cookie_header in response.headers.getlist("Set-Cookie"):
            if cookie_header.startswith(f"{CSRF_COOKIE_NAME}="):
                return response

    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            _new_csrf_token(),
            httponly=False,
            secure=request.is_secure,
            samesite="Strict",
            path="/",
        )
    return response


def _load_remote_access_config() -> V2Config | None:
    try:
        return V2Config.load()
    except Exception:
        logger.warning("Failed to load remote access config", exc_info=True)
        return None


def _is_local_request_host() -> bool:
    host = _normalized_host(request.host)
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _normalized_host(value: str | None) -> str:
    raw_host = (value or "").lower().strip()
    if raw_host.startswith("[") and "]" in raw_host:
        host = raw_host[1 : raw_host.index("]")]
    elif raw_host.count(":") > 1:
        host = raw_host
    else:
        host = raw_host.split(":", 1)[0]
    return host.rstrip(".")


def _is_remote_access_request(config: V2Config) -> bool:
    public_host = _remote_access_public_host(config)
    if not public_host:
        return False
    return _normalized_host(request.host) == public_host


def _remote_access_public_host(config: V2Config) -> str | None:
    public_url = (config.remote_access.vibe_cloud.public_url or "").strip()
    if not public_url:
        return ""
    parsed = urlparse(public_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return _normalized_host(parsed.netloc)


def _remote_access_public_url_invalid(config: V2Config) -> bool:
    cloud = config.remote_access.vibe_cloud
    return bool(cloud.enabled and not _remote_access_public_host(config))


def _remote_access_snapshot(config: V2Config) -> dict[str, Any]:
    return {
        "provider": config.remote_access.provider,
        "vibe_cloud": config.remote_access.vibe_cloud.__dict__.copy(),
    }


def _remote_access_settings_changed(previous: V2Config | None, current: V2Config, payload: dict) -> bool:
    if "remote_access" not in payload:
        return False
    if previous is None:
        return bool(_remote_access_snapshot(current)["vibe_cloud"].get("enabled"))
    return _remote_access_snapshot(previous) != _remote_access_snapshot(current)


def _should_rotate_remote_session_secret(previous: V2Config | None, current: V2Config, payload: dict) -> bool:
    if "remote_access" not in payload or previous is None:
        return False
    previous_cloud = previous.remote_access.vibe_cloud
    current_cloud = current.remote_access.vibe_cloud
    return bool(previous_cloud.enabled and not current_cloud.enabled and current_cloud.session_secret)


def _remote_auth_exempt_path() -> bool:
    path = request.path
    return (
        path == "/health"
        or path == "/auth/callback"
        or path.startswith("/assets/")
        or path == "/favicon.ico"
    )


def _oauth_cookie_signature(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _make_oauth_cookie(secret: str, payload: dict[str, Any]) -> str:
    payload_text = quote(json.dumps(payload, separators=(",", ":")), safe="")
    signature = _oauth_cookie_signature(secret, payload_text)
    return f"{payload_text}.{signature}"


def _read_oauth_cookie(secret: str, value: str | None) -> dict[str, Any] | None:
    if not value or "." not in value:
        return None
    payload_text, signature = value.rsplit(".", 1)
    if not hmac.compare_digest(signature, _oauth_cookie_signature(secret, payload_text)):
        return None
    try:
        payload = json.loads(unquote(payload_text))
    except Exception:
        return None
    if int(payload.get("exp", 0)) <= int(datetime.now().timestamp()):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_remote_redirect_target(value: Any) -> str:
    if not isinstance(value, str):
        return "/"
    target = value.strip()
    if not target.startswith("/") or target.startswith(("//", "/\\")):
        return "/"
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return "/"
    return urlunsplit(("", "", parsed.path or "/", parsed.query, ""))


def _redirect_to_vibe_cloud_login(config: V2Config):
    from vibe import remote_access

    cloud = config.remote_access.vibe_cloud
    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    oauth_cookie = _make_oauth_cookie(
        cloud.session_secret,
        {
            "state": state,
            "nonce": nonce,
            "code_verifier": code_verifier,
            "next": request.full_path if request.query_string else request.path,
            "exp": int(datetime.now().timestamp()) + 300,
        },
    )
    response = Response(status=302)
    response.headers["Location"] = remote_access.authorization_url(config, state, nonce, code_challenge)
    response.set_cookie(
        REMOTE_OAUTH_COOKIE_NAME,
        oauth_cookie,
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
    )
    return response


@app.before_request
def enforce_remote_access_cookie():
    config = _load_remote_access_config()
    if config is None:
        if _is_local_request_host():
            return None
        return jsonify({"ok": False, "error": "remote_access_config_unavailable"}), 503
    if _remote_access_public_url_invalid(config) and not _is_local_request_host():
        return jsonify({"ok": False, "error": "remote_access_public_url_invalid"}), 503
    if not _is_remote_access_request(config) or _remote_auth_exempt_path():
        return None
    from vibe import remote_access

    if not config.remote_access.vibe_cloud.enabled:
        return jsonify({"ok": False, "error": "remote_access_disabled"}), 503
    if not config.remote_access.vibe_cloud.session_secret:
        return jsonify({"ok": False, "error": "remote_access_session_secret_missing"}), 503
    if remote_access.validate_session_cookie(config, request.cookies.get(remote_access.SESSION_COOKIE_NAME)):
        return None
    if request.method == "GET":
        return _redirect_to_vibe_cloud_login(config)
    return jsonify({"ok": False, "error": "remote_access_login_required"}), 401


@app.before_request
def protect_mutating_ui_requests():
    if request.method not in MUTATING_METHODS:
        return None
    if _is_mutation_guard_exempt():
        return None

    source = _request_origin(request.headers.get("Origin")) or _request_origin(request.headers.get("Referer"))
    if not source:
        return jsonify({"ok": False, "message": "Forbidden: missing origin header"}), 403

    if source != _current_origin():
        return jsonify({"ok": False, "message": "Forbidden: invalid origin"}), 403

    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME, "")
    csrf_header = request.headers.get(CSRF_HEADER_NAME, "")
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        return jsonify({"ok": False, "message": "Forbidden: invalid csrf token"}), 403

    return None


@app.after_request
def add_csrf_cookie(response: Response) -> Response:
    return _ensure_csrf_cookie(response)


def _read_log_entries(log_path: Path, source_key: str, lines: int) -> tuple[list[dict[str, Any]], int]:
    if not log_path.exists():
        return [], 0

    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
    file_sort_ns = log_path.stat().st_mtime_ns
    first_recent_line_index = len(all_lines) - len(recent_lines)

    logs_list: list[dict[str, Any]] = []
    for line_offset, raw_line in enumerate(recent_lines):
        line = raw_line.rstrip("\n")
        match = STRUCTURED_LOG_PATTERN.match(line)
        if match:
            parsed_timestamp = match.group(1)
            logs_list.append(
                {
                    "timestamp": parsed_timestamp,
                    "logger": match.group(2),
                    "level": match.group(3),
                    "message": match.group(4),
                    "source": source_key,
                    "_sort_ns": _timestamp_to_sort_ns(parsed_timestamp) or file_sort_ns,
                    "_sort_index": first_recent_line_index + line_offset,
                }
            )
            continue

        if not line:
            continue

        if logs_list and _is_continuation_line(line, logs_list[-1]["message"]):
            logs_list[-1]["message"] += "\n" + line
            continue

        fallback_entry = _fallback_log_entry(line, source_key)
        fallback_entry["_sort_ns"] = file_sort_ns
        fallback_entry["_sort_index"] = first_recent_line_index + line_offset
        logs_list.append(fallback_entry)

    return logs_list, len(all_lines)


def _resolve_log_sources() -> list[dict[str, Any]]:
    resolved = [
        {
            "key": "all",
            "filename": "*",
            "path": "",
            "exists": True,
        }
    ]
    for key, filename, path_factory in LOG_SOURCES:
        path = path_factory()
        resolved.append(
            {
                "key": key,
                "filename": filename,
                "path": str(path),
                "exists": path.exists(),
            }
        )
    return resolved


# =============================================================================
# Error Handler
# =============================================================================


@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler - ensures all errors return JSON."""
    from werkzeug.exceptions import HTTPException

    # Preserve HTTP status codes for client errors (4xx)
    if isinstance(e, HTTPException):
        return jsonify({"error": e.description}), e.code

    # Log and return 500 for unexpected server errors
    logger.exception("Unhandled exception in UI server")
    return jsonify({"error": str(e)}), 500


# =============================================================================
# GET Endpoints
# =============================================================================


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/status")
def status():
    from vibe import runtime

    payload = runtime.read_status()
    pid_path = paths.get_runtime_pid_path()
    pid = pid_path.read_text(encoding="utf-8").strip() if pid_path.exists() else None
    try:
        running = bool(pid and pid.isdigit() and runtime.pid_alive(int(pid)))
    except Exception as exc:
        logger.warning("Failed to inspect service pid %s: %s", pid, exc)
        running = False
    payload["running"] = running
    payload["pid"] = int(pid) if pid and pid.isdigit() else None
    if running:
        payload["service_pid"] = payload.get("service_pid") or payload["pid"]
    elif payload.get("state") == "running":
        runtime.write_status("stopped", "process not running", None, payload.get("ui_pid"))
        payload = runtime.read_status()
        payload["running"] = False
        payload["pid"] = None
    return jsonify(payload)


@app.route("/doctor", methods=["GET"])
def doctor_get():
    payload = {}
    doctor_path = paths.get_runtime_doctor_path()
    if doctor_path.exists():
        payload = json.loads(doctor_path.read_text(encoding="utf-8"))
    return jsonify(payload)


@app.route("/config", methods=["GET"])
def config_get():
    from vibe import api

    config = api.load_config()
    return jsonify(api.config_to_payload(config))


@app.route("/platforms", methods=["GET"])
def platforms_get():
    from vibe import api

    return jsonify(api.get_platform_catalog())


@app.route("/settings", methods=["GET"])
def settings_get():
    from vibe import api

    return jsonify(api.get_settings(request.args.get("platform") or None))


@app.route("/api/csrf-token", methods=["GET"])
def csrf_token_get():
    token = request.cookies.get(CSRF_COOKIE_NAME) or _new_csrf_token()
    response = jsonify({"ok": True, "csrf_token": token})
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        httponly=False,
        secure=request.is_secure,
        samesite="Strict",
        path="/",
    )
    return response


@app.route("/cli/detect")
def cli_detect():
    from vibe import api

    binary = request.args.get("binary", "")
    return jsonify(api.detect_cli(binary))


@app.route("/slack/manifest")
def slack_manifest():
    from vibe import api

    return jsonify(api.get_slack_manifest())


@app.route("/version")
def version():
    from vibe import api

    return jsonify(api.get_version_info())


# =============================================================================
# POST Endpoints
# =============================================================================


@app.route("/control", methods=["POST"])
def control():
    from vibe import runtime
    from vibe.cli import _stop_opencode_server

    payload = request.json or {}
    action = payload.get("action")
    status = runtime.read_status()
    status["last_action"] = action
    if action == "start":
        runtime.ensure_config()
        runtime.stop_service()
        service_pid = runtime.start_service()
        runtime.write_status("running", "started", service_pid, status.get("ui_pid"))
    elif action == "stop":
        runtime.write_status("stopping", "stopping", status.get("service_pid"), status.get("ui_pid"))
        runtime.stop_service()
        _stop_opencode_server()
        runtime.write_status("stopped", "stopped", None, status.get("ui_pid"))
    elif action == "restart":
        import time

        runtime.write_status("restarting", "restarting", status.get("service_pid"), status.get("ui_pid"))
        runtime.stop_service()
        _stop_opencode_server()
        time.sleep(3)
        runtime.ensure_config()
        service_pid = runtime.start_service()
        runtime.write_status("running", "restarted", service_pid, status.get("ui_pid"))
    return jsonify({"ok": True, "action": action, "status": runtime.read_status()})


@app.route("/config", methods=["POST"])
def config_post():
    from vibe import api
    from vibe import remote_access

    payload = request.json or {}
    previous_config = _load_remote_access_config() if "remote_access" in payload else None
    config = api.save_config(payload)
    remote_access_runtime = None
    if _remote_access_settings_changed(previous_config, config, payload):
        if _should_rotate_remote_session_secret(previous_config, config, payload):
            remote_access.rotate_session_secret(config)
        remote_access_runtime = remote_access.reconcile(config)
    response_payload = api.config_to_payload(config)
    if remote_access_runtime is not None:
        response_payload["remote_access_runtime"] = remote_access_runtime
    return jsonify(response_payload)


@app.route("/remote-access/status", methods=["GET"])
def remote_access_status():
    from vibe import remote_access

    return jsonify(remote_access.status())


@app.route("/remote-access/vibe-cloud/pair", methods=["POST"])
def remote_access_vibe_cloud_pair():
    from vibe import remote_access

    payload = request.json or {}
    result = remote_access.pair(
        payload.get("pairing_key", ""),
        payload.get("backend_url", "https://avibe.bot"),
        payload.get("device_name", "Vibe Remote"),
    )
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/remote-access/start", methods=["POST"])
def remote_access_start():
    from vibe import remote_access

    result = remote_access.start()
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/remote-access/stop", methods=["POST"])
def remote_access_stop():
    from vibe import remote_access

    result = remote_access.stop()
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/auth/callback", methods=["GET"])
def remote_access_auth_callback():
    from vibe import remote_access

    config = _load_remote_access_config()
    if config is None or not _is_remote_access_request(config):
        return jsonify({"error": "remote_access_not_enabled"}), 400
    cloud = config.remote_access.vibe_cloud
    oauth_state = _read_oauth_cookie(cloud.session_secret, request.cookies.get(REMOTE_OAUTH_COOKIE_NAME))
    if not oauth_state or oauth_state.get("state") != request.args.get("state"):
        return jsonify({"error": "invalid_oauth_state"}), 400
    try:
        result = remote_access.exchange_oauth_code(config, request.args.get("code", ""), oauth_state["code_verifier"])
        claims = result["claims"]
    except Exception as exc:
        return jsonify({"error": "oauth_exchange_failed", "detail": str(exc)}), 400
    if claims.get("nonce") != oauth_state.get("nonce"):
        return jsonify({"error": "invalid_oauth_nonce"}), 400
    response = Response(status=302)
    response.headers["Location"] = _safe_remote_redirect_target(oauth_state.get("next"))
    response.set_cookie(
        remote_access.SESSION_COOKIE_NAME,
        remote_access.make_session_cookie(config, str(claims.get("email", "")), str(claims.get("sub", ""))),
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
        max_age=remote_access.SESSION_TTL_SECONDS,
    )
    response.delete_cookie(REMOTE_OAUTH_COOKIE_NAME, path="/", secure=True, samesite="Lax")
    return response


@app.route("/ui/reload", methods=["POST"])
def ui_reload():
    from vibe import runtime

    payload = request.json or {}
    host = payload.get("host")
    port = payload.get("port")
    if not host or not port:
        return jsonify({"error": "host_and_port_required"}), 400
    try:
        port = int(port)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_port"}), 400

    status = runtime.read_status()

    def _restart():
        global _server
        import subprocess
        import sys
        import time
        from config import paths as config_paths

        working_dir = get_working_dir()
        command = f"from vibe.ui_server import run_ui_server; run_ui_server('{host}', {port})"
        stdout_path = config_paths.get_runtime_dir() / "ui_stdout.log"
        stderr_path = config_paths.get_runtime_dir() / "ui_stderr.log"
        stdout = stdout_path.open("ab")
        stderr = stderr_path.open("ab")
        process = subprocess.Popen(
            [sys.executable, "-c", command],
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            cwd=str(working_dir),
            close_fds=True,
        )
        stdout.close()
        stderr.close()
        config_paths.get_runtime_ui_pid_path().write_text(str(process.pid), encoding="utf-8")
        runtime.write_status(
            status.get("state", "running"),
            status.get("detail"),
            status.get("service_pid"),
            process.pid,
        )
        time.sleep(0.2)
        # Shutdown the old server to release the port
        if _server:
            _server.shutdown()

    # Schedule restart after response is sent
    threading.Thread(target=_restart).start()
    return jsonify({"ok": True, "host": host, "port": port})


@app.route("/settings", methods=["POST"])
def settings_post():
    from vibe import api

    payload = request.json or {}
    return jsonify(api.save_settings(payload))


@app.route("/slack/auth_test", methods=["POST"])
def slack_auth_test():
    from vibe import api

    payload = request.json or {}
    result = api.slack_auth_test(payload.get("bot_token", ""))
    return jsonify(result)


@app.route("/slack/channels", methods=["POST"])
def slack_channels():
    from vibe import api

    payload = request.json or {}
    return jsonify(
        api.list_channels(
            payload.get("bot_token", ""),
            browse_all=payload.get("browse_all", False),
        )
    )


@app.route("/discord/auth_test", methods=["POST"])
def discord_auth_test():
    from vibe import api

    payload = request.json or {}
    result = api.discord_auth_test(payload.get("bot_token", ""))
    return jsonify(result)


@app.route("/discord/guilds", methods=["POST"])
def discord_guilds():
    from vibe import api

    payload = request.json or {}
    return jsonify(api.discord_list_guilds(payload.get("bot_token", "")))


@app.route("/discord/channels", methods=["POST"])
def discord_channels():
    from vibe import api

    payload = request.json or {}
    return jsonify(api.discord_list_channels(payload.get("bot_token", ""), payload.get("guild_id", "")))


@app.route("/telegram/auth_test", methods=["POST"])
def telegram_auth_test():
    from vibe import api

    payload = request.json or {}
    result = api.telegram_auth_test(payload.get("bot_token", ""))
    return jsonify(result)


@app.route("/telegram/chats", methods=["POST"])
def telegram_chats():
    from vibe import api

    payload = request.json or {}
    return jsonify(api.telegram_list_chats(include_private=payload.get("include_private", False)))


@app.route("/lark/auth_test", methods=["POST"])
def lark_auth_test():
    from vibe import api

    payload = request.json or {}
    result = api.lark_auth_test(
        payload.get("app_id", ""), payload.get("app_secret", ""), payload.get("domain", "feishu")
    )
    return jsonify(result)


@app.route("/lark/chats", methods=["POST"])
def lark_chats():
    from vibe import api

    payload = request.json or {}
    return jsonify(
        api.lark_list_chats(payload.get("app_id", ""), payload.get("app_secret", ""), payload.get("domain", "feishu"))
    )


@app.route("/lark/temp_ws/start", methods=["POST"])
def lark_temp_ws_start():
    from vibe import api

    payload = request.json or {}
    return jsonify(
        api.lark_temp_ws_start(
            payload.get("app_id", ""), payload.get("app_secret", ""), payload.get("domain", "feishu")
        )
    )


@app.route("/lark/temp_ws/stop", methods=["POST"])
def lark_temp_ws_stop():
    from vibe import api

    return jsonify(api.lark_temp_ws_stop())


# WeChat auth singleton
_wechat_auth_manager = None


def _get_wechat_auth():
    global _wechat_auth_manager
    if _wechat_auth_manager is None:
        from modules.im.wechat_auth import WeChatAuthManager

        _wechat_auth_manager = WeChatAuthManager()
    return _wechat_auth_manager


@app.route("/wechat/qr_login/start", methods=["POST"])
def wechat_qr_login_start():
    """Start WeChat QR code login flow."""
    auth = _get_wechat_auth()
    payload = request.json or {}
    base_url = payload.get("base_url", "https://ilinkai.weixin.qq.com")

    result = _run_async(auth.start_login(base_url=base_url), timeout=15.0)
    if result.get("ok") is False:
        return jsonify(result), 500
    return jsonify(result)


@app.route("/wechat/qr_login/poll", methods=["POST"])
def wechat_qr_login_poll():
    """Poll WeChat QR code login status."""
    payload = request.json or {}
    session_key = payload.get("session_key", "")
    if not session_key:
        return jsonify({"error": "session_key required"}), 400

    auth = _get_wechat_auth()
    result = _run_async(auth.poll_status(session_key), timeout=15.0)
    if result.get("ok") is False:
        return jsonify(result), 500

    # If confirmed, auto-bind the WeChat user
    if result.get("status") == "confirmed" and result.get("bot_token"):
        user_id = result.get("user_id", "wechat_user")

        # Auto-bind user
        try:
            from vibe import api as vibe_api

            vibe_api.auto_bind_wechat_user(user_id)
        except Exception as e:
            logger.warning("Failed to auto-bind WeChat user: %s", e)

        # Schedule service restart so the new token takes effect
        def _restart_after_login():
            import time

            time.sleep(2)  # let the response go out first
            try:
                from vibe import runtime

                runtime.stop_service()
                time.sleep(1)
                runtime.ensure_config()
                service_pid = runtime.start_service()
                st = runtime.read_status()
                runtime.write_status("running", "restarted", service_pid, st.get("ui_pid"))
                logger.info("Service restarted after WeChat QR login")
            except Exception as exc:
                logger.warning("Failed to restart service after QR login: %s", exc)

        threading.Thread(target=_restart_after_login, daemon=True).start()

    return jsonify(result)


@app.route("/doctor", methods=["POST"])
def doctor_post():
    from vibe.cli import _doctor

    result = _doctor()
    return jsonify(result)


@app.route("/logs", methods=["POST"])
def logs():
    payload = request.json or {}
    try:
        lines = max(int(payload.get("lines", 500)), 1)
    except (TypeError, ValueError):
        lines = 500
    selected_source = payload.get("source", "service")
    sources = _resolve_log_sources()
    source_map = {source["key"]: source for source in sources}
    active_source = source_map.get(selected_source) or source_map["all"]

    try:
        aggregated_logs: list[dict[str, Any]] = []
        aggregated_total = 0
        for source in sources:
            if source["key"] == "all":
                continue
            source_logs, total = _read_log_entries(Path(source["path"]), source["key"], lines)
            source["total"] = total
            aggregated_logs.extend(source_logs)
            aggregated_total += total
            if source["key"] == active_source["key"]:
                source["logs"] = source_logs
                active_logs = source_logs
                active_total = total
            else:
                source["logs"] = []
        sources[0]["total"] = aggregated_total
        sources[0]["logs"] = []
        if active_source["key"] == "all":
            active_logs = sorted(
                aggregated_logs,
                key=lambda entry: (
                    int(entry.get("_sort_ns", 0)),
                    int(entry.get("_sort_index", 0)),
                    entry.get("source") or "",
                    entry.get("logger") or "",
                ),
            )
            if len(active_logs) > lines:
                active_logs = active_logs[-lines:]
            active_total = aggregated_total
        return jsonify(
            {
                "source": active_source["key"],
                "logs": _serialize_log_entries(active_logs),
                "total": active_total,
                "sources": sources,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/opencode/options", methods=["POST"])
def opencode_options():
    from vibe import api

    payload = request.json or {}
    result = _run_async(
        api.opencode_options_async(payload.get("cwd", ".")),
        timeout=12.0,
    )
    return jsonify(result)


@app.route("/upgrade", methods=["POST"])
def upgrade():
    from vibe import api

    result = api.do_upgrade()
    return jsonify(result)


@app.route("/opencode/setup-permission", methods=["POST"])
def opencode_setup_permission():
    from vibe import api

    return jsonify(api.setup_opencode_permission())


@app.route("/claude/agents", methods=["GET"])
def claude_agents():
    from vibe import api

    cwd = request.args.get("cwd")
    if cwd:
        # Expand ~ first, then check if absolute
        expanded = Path(cwd).expanduser()
        if not expanded.is_absolute():
            cwd = str(get_working_dir() / cwd)
        else:
            cwd = str(expanded)

    return jsonify(api.claude_agents(cwd))


@app.route("/codex/agents", methods=["GET"])
def codex_agents():
    from vibe import api

    cwd = request.args.get("cwd")
    if cwd:
        expanded = Path(cwd).expanduser()
        if not expanded.is_absolute():
            cwd = str(get_working_dir() / cwd)
        else:
            cwd = str(expanded)

    return jsonify(api.codex_agents(cwd))


@app.route("/claude/models", methods=["GET"])
def claude_models():
    from vibe import api

    return jsonify(api.claude_models())


@app.route("/codex/models", methods=["GET"])
def codex_models():
    from vibe import api

    return jsonify(api.codex_models())


@app.route("/agent/<name>/install", methods=["POST"])
def agent_install(name):
    """Install an agent CLI tool (opencode, claude, codex)."""
    # Security: Allowlist validation
    allowed_agents = {"opencode", "claude", "codex"}
    if name not in allowed_agents:
        return jsonify({"ok": False, "message": f"Unknown agent: {name}"}), 400

    from vibe import api

    result = api.install_agent(name)
    return jsonify(result)


@app.route("/browse", methods=["POST"])
def browse_directory():
    """List sub-directories of a given path for the directory picker UI."""
    from vibe import api

    payload = request.json or {}
    return jsonify(
        api.browse_directory(
            payload.get("path", "~"),
            show_hidden=bool(payload.get("show_hidden", False)),
        )
    )


# =============================================================================
# User & Bind Code Endpoints
# =============================================================================


@app.route("/api/users", methods=["GET"])
def users_get():
    from vibe import api

    return jsonify(api.get_users(request.args.get("platform") or None))


@app.route("/api/users", methods=["POST"])
def users_post():
    from vibe import api

    payload = request.json or {}
    return jsonify(api.save_users(payload))


@app.route("/api/users/<user_id>/admin", methods=["POST"])
def users_toggle_admin(user_id):
    from vibe import api

    payload = request.json or {}
    return jsonify(api.toggle_admin(user_id, payload.get("is_admin", False), payload.get("platform") or None))


@app.route("/api/users/<user_id>", methods=["DELETE"])
def users_delete(user_id):
    from vibe import api

    result = api.remove_user(user_id, request.args.get("platform") or None)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/bind-codes", methods=["GET"])
def bind_codes_get():
    from vibe import api

    return jsonify(api.get_bind_codes())


@app.route("/api/bind-codes", methods=["POST"])
def bind_codes_post():
    from vibe import api

    payload = request.json or {}
    result = api.create_bind_code(
        code_type=payload.get("type", "one_time"),
        expires_at=payload.get("expires_at"),
    )
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/bind-codes/<code>", methods=["DELETE"])
def bind_codes_delete(code):
    from vibe import api

    result = api.delete_bind_code(code)
    if not result.get("ok"):
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/setup/first-bind-code", methods=["GET"])
def setup_first_bind_code():
    from vibe import api

    return jsonify(api.get_first_bind_code())


# =============================================================================
# E2E Test-Only Endpoints (gated by E2E_TEST_MODE env var)
# =============================================================================

if os.environ.get("E2E_TEST_MODE", "").lower() in ("true", "1", "yes"):
    logger.warning(
        "E2E_TEST_MODE is ENABLED. /e2e/* endpoints are registered. "
        "These endpoints allow unauthenticated config mutation. "
        "Do NOT enable in production."
    )

    @app.route("/e2e/simulate-interaction", methods=["POST"])
    def e2e_simulate_interaction():
        """Simulate a modal submission via the settings/config APIs.

        Only registered when E2E_TEST_MODE=true.

        NOTE: Button clicks (cmd_settings, cmd_routing, etc.) should be
        triggered by sending text commands via Bot B (/settings, /routing, etc.).
        This endpoint handles modal *submissions* that Bot B cannot trigger
        because they require UI interaction (select dropdowns, click Save).

        The UI server and the service process are separate processes, so this
        endpoint operates through the SettingsStore (shared JSON file) rather
        than invoking the controller directly.

        JSON fields:
            action (str):       "settings_submit" | "routing_submit" | "cwd_submit"
            modal_values (dict): the values to submit
        """
        payload = request.json or {}
        action = payload.get("action", "")
        modal_values = payload.get("modal_values", {})

        if not action:
            return jsonify({"ok": False, "error": "action required"}), 400

        try:
            if action == "settings_submit":
                # Merge settings into existing store (not wholesale replace)
                from config.v2_settings import SettingsStore, ChannelSettings, normalize_show_message_types
                from vibe.api import _parse_routing
                from vibe.api import _current_platform

                settings_key = modal_values.get("settings_key") or modal_values.get("channel_id")
                if not settings_key:
                    return jsonify({"ok": False, "error": "settings_key or channel_id required in modal_values"}), 400

                store = SettingsStore.get_instance()
                store.maybe_reload()
                platform = _current_platform()
                ch = store.find_channel(settings_key, platform=platform)
                if not ch:
                    ch = ChannelSettings(enabled=True)
                    store.update_channel(settings_key, ch, platform=platform)

                if "show_message_types" in modal_values:
                    ch.show_message_types = normalize_show_message_types(modal_values["show_message_types"])
                if "custom_cwd" in modal_values:
                    ch.custom_cwd = modal_values["custom_cwd"]
                if "require_mention" in modal_values:
                    ch.require_mention = modal_values["require_mention"]
                if "routing" in modal_values:
                    ch.routing = _parse_routing(modal_values["routing"])

                store.save()
                return jsonify({"ok": True, "action": action})

            elif action == "routing_submit":
                # Write routing config for a specific channel/user
                channel_id = modal_values.get("channel_id") or modal_values.get("settings_key")
                if not channel_id:
                    return jsonify({"ok": False, "error": "channel_id required in modal_values"}), 400

                store = SettingsStore.get_instance()
                store.maybe_reload()
                from vibe.api import _current_platform

                platform = _current_platform()
                ch = store.find_channel(channel_id, platform=platform)
                if ch:
                    from config.v2_settings import RoutingSettings

                    ch.routing = RoutingSettings(
                        agent_backend=modal_values.get("backend", "opencode"),
                        opencode_agent=modal_values.get("opencode_agent"),
                        opencode_model=modal_values.get("opencode_model"),
                        opencode_reasoning_effort=modal_values.get("opencode_reasoning_effort"),
                        claude_agent=modal_values.get("claude_agent"),
                        claude_model=modal_values.get("claude_model"),
                        claude_reasoning_effort=modal_values.get("claude_reasoning_effort"),
                        codex_agent=modal_values.get("codex_agent"),
                        codex_model=modal_values.get("codex_model"),
                        codex_reasoning_effort=modal_values.get("codex_reasoning_effort"),
                    )
                    store.save()
                    return jsonify({"ok": True, "action": action})
                else:
                    return jsonify({"ok": False, "error": f"channel {channel_id} not found in settings"}), 404

            elif action == "cwd_submit":
                # Merge CWD into existing config (load → modify → save)
                from vibe import api as vibe_api

                current = vibe_api.config_to_payload(vibe_api.load_config())
                current.setdefault("runtime", {})
                current["runtime"]["default_cwd"] = modal_values.get("cwd", "/tmp")
                result = vibe_api.save_config(current)
                return jsonify({"ok": True, "action": action})

            elif action == "routing_submit":
                # Write routing config for a specific channel/user
                channel_id = modal_values.get("channel_id") or modal_values.get("settings_key")
                if not channel_id:
                    return jsonify({"ok": False, "error": "channel_id required in modal_values"}), 400

                store = SettingsStore.get_instance()
                store.maybe_reload()
                from vibe.api import _current_platform

                platform = _current_platform()
                ch = store.find_channel(channel_id, platform=platform)
                if ch:
                    from config.v2_settings import RoutingSettings

                    ch.routing = RoutingSettings(
                        agent_backend=modal_values.get("backend", "opencode"),
                        opencode_agent=modal_values.get("opencode_agent"),
                        opencode_model=modal_values.get("opencode_model"),
                        opencode_reasoning_effort=modal_values.get("opencode_reasoning_effort"),
                        claude_agent=modal_values.get("claude_agent"),
                        claude_model=modal_values.get("claude_model"),
                        claude_reasoning_effort=modal_values.get("claude_reasoning_effort"),
                        codex_agent=modal_values.get("codex_agent"),
                        codex_model=modal_values.get("codex_model"),
                        codex_reasoning_effort=modal_values.get("codex_reasoning_effort"),
                    )
                    store.save()
                    return jsonify({"ok": True, "action": action})
                else:
                    return jsonify({"ok": False, "error": f"channel {channel_id} not found in settings"}), 404

            elif action == "cwd_submit":
                # Update CWD via config API
                new_cwd = modal_values.get("cwd", "/tmp")
                result = vibe_api.save_config({"runtime": {"default_cwd": new_cwd}})
                return jsonify({"ok": True, "action": action, "result": result})

            else:
                return jsonify({"ok": False, "error": f"unknown action: {action}"}), 400

        except Exception as e:
            logger.exception("E2E simulate-interaction failed")
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/e2e/ping", methods=["GET"])
    def e2e_ping():
        """Simple check that E2E test mode is active."""
        return jsonify({"ok": True, "e2e_test_mode": True})

    logger.info("E2E_TEST_MODE enabled: /e2e/* endpoints registered")


# =============================================================================
# Static Files (SPA)
# =============================================================================


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_static(path):
    """Serve static files from ui/dist, with SPA fallback to index.html."""
    ui_dist = get_ui_dist_path()

    if path.startswith("assets/"):
        file_path = ui_dist / path
    elif not path or path == "index.html":
        file_path = ui_dist / "index.html"
    else:
        file_path = ui_dist / path

    resolved_path = file_path.resolve()

    # Security check: ensure path is within ui_dist
    if ui_dist.resolve() not in resolved_path.parents and resolved_path != ui_dist.resolve():
        return jsonify({"error": "not_found"}), 404

    if resolved_path.exists() and resolved_path.is_file():
        mime_type, _ = mimetypes.guess_type(str(resolved_path))
        return send_file(resolved_path, mimetype=mime_type or "application/octet-stream")

    # SPA fallback: serve index.html for routes without file extension
    if "." not in path:
        index_path = ui_dist / "index.html"
        if index_path.exists():
            return send_file(index_path, mimetype="text/html")

    return jsonify({"error": "not_found"}), 404


# =============================================================================
# Server Entry Point
# =============================================================================


def run_ui_server(host: str, port: int) -> None:
    """Start the Flask UI server."""
    global _server
    import time
    from werkzeug.serving import make_server

    paths.ensure_data_dirs()
    try:
        config = V2Config.load()
    except FileNotFoundError:
        config = None
    except Exception as exc:
        logger.warning("Skipping UI Sentry init because config load failed: %s", exc)
        config = None
    if config is not None:
        init_sentry(config, component="ui", enable_flask=True)
    print(f"UI Server running at http://{host}:{port}")

    # Use make_server directly for better compatibility with subprocess/multiprocessing
    # app.run() has issues when launched in child processes
    # Retry binding in case of TIME_WAIT or port still held by old server during reload
    for attempt in range(10):
        try:
            _server = make_server(host, port, app, threaded=True)
            _server.serve_forever()
            break
        except OSError as e:
            if e.errno == 48 and attempt < 9:  # Address already in use (macOS)
                print(f"Port {port} in use, retrying in 1s... (attempt {attempt + 1})")
                time.sleep(1)
            elif e.errno == 98 and attempt < 9:  # Address already in use (Linux)
                print(f"Port {port} in use, retrying in 1s... (attempt {attempt + 1})")
                time.sleep(1)
            else:
                raise
