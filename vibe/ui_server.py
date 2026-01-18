import http.server
import json
import socketserver
import mimetypes
from pathlib import Path

from config import paths


class UiHandler(http.server.BaseHTTPRequestHandler):
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
            payload = {}
            status_path = paths.get_runtime_status_path()
            if status_path.exists():
                payload = json.loads(status_path.read_text(encoding="utf-8"))
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

        ui_dist = Path("ui/dist")
        requested_path = self.path.lstrip("/")
        if requested_path.startswith("assets/"):
            file_path = ui_dist / requested_path
        elif not requested_path or requested_path == "index.html":
            file_path = ui_dist / "index.html"
        else:
            file_path = ui_dist / requested_path
        resolved_path = file_path.resolve()
        if ui_dist.resolve() not in resolved_path.parents and resolved_path != ui_dist.resolve():
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

            payload = self._read_json()
            action = payload.get("action")
            status = runtime.read_status()
            status["last_action"] = action
            if action == "start":
                config = runtime.ensure_config()
                runtime.stop_service()
                service_pid = runtime.start_service()
                runtime.write_status("running", "started", service_pid, status.get("ui_pid"))
            elif action == "stop":
                runtime.stop_service()
                runtime.write_status("stopped")
            elif action == "restart":
                runtime.stop_service()
                config = runtime.ensure_config()
                service_pid = runtime.start_service()
                runtime.write_status("running", "restarted", service_pid, status.get("ui_pid"))
            self._send_json({"ok": True, "action": action, "status": runtime.read_status()})
            return
        if self.path == "/config":
            from vibe import api

            payload = self._read_json()
            config = api.save_config(payload)
            api.init_sessions()
            self._send_json(api.config_to_payload(config))
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
        if self.path == "/opencode/options":
            from vibe import api

            payload = self._read_json()
            self._send_json(api.opencode_options(payload.get("cwd", ".")))
            return
        self._send_json({"error": "not_found"}, status=404)


def run_ui_server(port: int) -> None:
    paths.ensure_data_dirs()
    print(f"UI Server running at http://127.0.0.1:{port}")
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), UiHandler) as httpd:
        httpd.serve_forever()
