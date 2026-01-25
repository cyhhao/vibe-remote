import http.server
import json
import socketserver
import mimetypes
import threading
from pathlib import Path
from typing import Any

from config import paths
from vibe.runtime import get_ui_dist_path, get_working_dir


class UiHandler(http.server.BaseHTTPRequestHandler):
    server_version = "VibeUI"

    def log_message(self, format: str, *args):
        return

    def _run_async(self, coro, timeout: float = 10.0):
        result: dict[str, Any] = {}
        error: str | None = None
        lock = threading.Event()

        def _runner():
            nonlocal result, error
            try:
                import asyncio

                result = asyncio.run(coro)
            except Exception as exc:
                error = str(exc)
            finally:
                lock.set()

        threading.Thread(target=_runner, daemon=True).start()
        lock.wait(timeout=timeout)
        if not lock.is_set():
            return {"ok": False, "error": "OpenCode options request timed out"}
        if error:
            return {"ok": False, "error": error}
        return result

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length) if length else b"{}"
        return json.loads(data.decode("utf-8"))

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return
        if self.path == "/status":
            from vibe import runtime

            payload = runtime.read_status()
            pid_path = paths.get_runtime_pid_path()
            pid = (
                pid_path.read_text(encoding="utf-8").strip()
                if pid_path.exists()
                else None
            )
            running = bool(pid and pid.isdigit() and runtime.pid_alive(int(pid)))
            payload["running"] = running
            payload["pid"] = int(pid) if pid and pid.isdigit() else None
            if running:
                payload["service_pid"] = payload.get("service_pid") or payload["pid"]
            elif payload.get("state") == "running":
                runtime.write_status(
                    "stopped", "process not running", None, payload.get("ui_pid")
                )
                payload = runtime.read_status()
                payload["running"] = False
                payload["pid"] = None
            self._send_json(payload)
            return
        if self.path == "/doctor":
            payload = {}
            doctor_path = paths.get_runtime_doctor_path()
            if doctor_path.exists():
                payload = json.loads(doctor_path.read_text(encoding="utf-8"))
            self._send_json(payload)
            return
        if self.path == "/config":
            from vibe import api

            try:
                config = api.load_config()
                self._send_json(api.config_to_payload(config))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        if self.path == "/settings":
            from vibe import api

            self._send_json(api.get_settings())
            return
        if self.path.startswith("/cli/detect"):
            from urllib.parse import urlparse, parse_qs
            from vibe import api

            query = parse_qs(urlparse(self.path).query)
            binary = (query.get("binary") or [""])[0]
            self._send_json(api.detect_cli(binary))
            return
        if self.path == "/slack/manifest":
            from vibe import api

            self._send_json(api.get_slack_manifest())
            return
        if self.path == "/version":
            from vibe import api

            self._send_json(api.get_version_info())
            return

        ui_dist = get_ui_dist_path()
        requested_path = self.path.lstrip("/")
        if requested_path.startswith("assets/"):
            file_path = ui_dist / requested_path
        elif not requested_path or requested_path == "index.html":
            file_path = ui_dist / "index.html"
        else:
            file_path = ui_dist / requested_path
        resolved_path = file_path.resolve()
        if (
            ui_dist.resolve() not in resolved_path.parents
            and resolved_path != ui_dist.resolve()
        ):
            self._send_json({"error": "not_found"}, status=404)
            return

        if resolved_path.exists() and resolved_path.is_file():
            content = resolved_path.read_bytes()
            mime_type, _ = mimetypes.guess_type(str(resolved_path))
            self.send_response(200)
            self.send_header("Content-Type", mime_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if "." not in requested_path:
            index_path = ui_dist / "index.html"
            if index_path.exists():
                content = index_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        self._send_json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:
        if self.path == "/control":
            from vibe import runtime
            from vibe.cli import _stop_opencode_server

            payload = self._read_json()
            action = payload.get("action")
            status = runtime.read_status()
            status["last_action"] = action
            if action == "start":
                config = runtime.ensure_config()
                runtime.stop_service()
                service_pid = runtime.start_service()
                runtime.write_status(
                    "running", "started", service_pid, status.get("ui_pid")
                )
            elif action == "stop":
                runtime.stop_service()
                # Also terminate OpenCode server on full stop
                _stop_opencode_server()
                runtime.write_status("stopped")
            elif action == "restart":
                runtime.stop_service()
                config = runtime.ensure_config()
                service_pid = runtime.start_service()
                runtime.write_status(
                    "running", "restarted", service_pid, status.get("ui_pid")
                )
            self._send_json(
                {"ok": True, "action": action, "status": runtime.read_status()}
            )
            return
        if self.path == "/config":
            from vibe import api

            payload = self._read_json()
            config = api.save_config(payload)
            api.init_sessions()
            self._send_json(api.config_to_payload(config))
            return
        if self.path == "/ui/reload":
            from vibe import runtime

            payload = self._read_json()
            host = payload.get("host")
            port = payload.get("port")
            if not host or not port:
                self._send_json({"error": "host_and_port_required"}, status=400)
                return
            try:
                port = int(port)
            except (TypeError, ValueError):
                self._send_json({"error": "invalid_port"}, status=400)
                return
            status = runtime.read_status()
            self._send_json({"ok": True, "host": host, "port": port})

            def _restart():
                import subprocess
                import sys
                import time
                from config import paths as config_paths
                from vibe.runtime import get_working_dir

                working_dir = get_working_dir()
                # Start new UI server process first (it will retry until port is available)
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
                # Write new PID
                config_paths.get_runtime_ui_pid_path().write_text(
                    str(process.pid), encoding="utf-8"
                )
                runtime.write_status(
                    status.get("state", "running"),
                    status.get("detail"),
                    status.get("service_pid"),
                    process.pid,
                )
                # Give the new process a moment to start attempting connection
                time.sleep(0.2)
                # Now shutdown current server - new process will retry until port is free
                self.server.shutdown()
                self.server.server_close()

            threading.Thread(target=_restart).start()
            return
        if self.path == "/settings":
            from vibe import api

            payload = self._read_json()
            self._send_json(api.save_settings(payload))
            return
        if self.path == "/slack/auth_test":
            from vibe import api

            payload = self._read_json()
            result = api.slack_auth_test(payload.get("bot_token", ""))
            self._send_json(result)
            return
        if self.path == "/slack/channels":
            from vibe import api

            payload = self._read_json()
            self._send_json(api.list_channels(payload.get("bot_token", "")))
            return
        if self.path == "/doctor":
            from vibe.cli import _doctor

            result = _doctor()
            self._send_json(result)
            return
        if self.path == "/logs":
            from config import paths
            import re

            payload = self._read_json()
            lines = payload.get("lines", 500)
            log_path = paths.get_logs_dir() / "vibe_remote.log"
            if not log_path.exists():
                self._send_json({"logs": [], "total": 0})
                return
            # Read last N lines
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                    recent_lines = (
                        all_lines[-lines:] if len(all_lines) > lines else all_lines
                    )
                # Parse log lines
                # Format: 2026-01-19 18:46:42,292 - slack_sdk.socket_mode.aiohttp - DEBUG - [__init__.py:246] - message
                log_pattern = re.compile(
                    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+-\s+([\w.]+)\s+-\s+(\w+)\s+-\s+(.*)$"
                )
                logs = []
                for line in recent_lines:
                    line = line.rstrip("\n")
                    match = log_pattern.match(line)
                    if match:
                        logs.append(
                            {
                                "timestamp": match.group(1),
                                "logger": match.group(2),
                                "level": match.group(3),
                                "message": match.group(4),
                            }
                        )
                    elif logs and line:
                        # Continuation of previous log (multiline)
                        logs[-1]["message"] += "\n" + line
                self._send_json({"logs": logs, "total": len(all_lines)})
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return
        if self.path == "/opencode/options":
            from vibe import api

            payload = self._read_json()
            result = self._run_async(
                api.opencode_options_async(payload.get("cwd", ".")),
                timeout=12.0,
            )
            self._send_json(result)
            return
        if self.path == "/upgrade":
            from vibe import api

            result = api.do_upgrade()
            self._send_json(result)
            return
        if self.path == "/opencode/setup-permission":
            from vibe import api

            result = api.setup_opencode_permission()
            self._send_json(result)
            return
        self._send_json({"error": "not_found"}, status=404)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_ui_server(host: str, port: int) -> None:
    import time

    paths.ensure_data_dirs()
    print(f"UI Server running at http://{host}:{port}")
    # Retry binding in case of TIME_WAIT
    for attempt in range(10):
        try:
            with ThreadingHTTPServer((host, port), UiHandler) as httpd:
                httpd.serve_forever()
            break
        except OSError as e:
            if e.errno == 48 and attempt < 9:  # Address already in use
                print(f"Port {port} in use, retrying in 1s... (attempt {attempt + 1})")
                time.sleep(1)
            else:
                raise
