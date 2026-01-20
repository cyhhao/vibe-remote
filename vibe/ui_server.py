import asyncio
import json
import logging
import mimetypes
import re
import threading
from pathlib import Path
from typing import Any

from flask import Flask, request, jsonify, send_file, Response

from config import paths
from vibe.runtime import get_ui_dist_path, get_working_dir

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=None)

# Global server instance for graceful shutdown on reload
_server = None

# Disable Flask's default logging
log = logging.getLogger("werkzeug")
log.setLevel(logging.WARNING)


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


# =============================================================================
# Error Handler
# =============================================================================


@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler - ensures all errors return JSON."""
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
    running = bool(pid and pid.isdigit() and runtime.pid_alive(int(pid)))
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


@app.route("/settings", methods=["GET"])
def settings_get():
    from vibe import api

    return jsonify(api.get_settings())


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
        runtime.stop_service()
        _stop_opencode_server()
        runtime.write_status("stopped")
    elif action == "restart":
        runtime.stop_service()
        runtime.ensure_config()
        service_pid = runtime.start_service()
        runtime.write_status("running", "restarted", service_pid, status.get("ui_pid"))
    return jsonify({"ok": True, "action": action, "status": runtime.read_status()})


@app.route("/config", methods=["POST"])
def config_post():
    from vibe import api

    payload = request.json or {}
    config = api.save_config(payload)
    api.init_sessions()
    return jsonify(api.config_to_payload(config))


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
    return jsonify(api.list_channels(payload.get("bot_token", "")))


@app.route("/doctor", methods=["POST"])
def doctor_post():
    from vibe.cli import _doctor

    result = _doctor()
    return jsonify(result)


@app.route("/logs", methods=["POST"])
def logs():
    payload = request.json or {}
    lines = payload.get("lines", 500)
    log_path = paths.get_logs_dir() / "vibe_remote.log"
    if not log_path.exists():
        return jsonify({"logs": [], "total": 0})
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
        log_pattern = re.compile(
            r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+-\s+([\w.]+)\s+-\s+(\w+)\s+-\s+(.*)$"
        )
        logs_list = []
        for line in recent_lines:
            line = line.rstrip("\n")
            match = log_pattern.match(line)
            if match:
                logs_list.append({
                    "timestamp": match.group(1),
                    "logger": match.group(2),
                    "level": match.group(3),
                    "message": match.group(4),
                })
            elif logs_list and line:
                logs_list[-1]["message"] += "\n" + line
        return jsonify({"logs": logs_list, "total": len(all_lines)})
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
