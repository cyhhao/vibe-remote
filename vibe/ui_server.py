import http.server
import json
import socketserver
from typing import Optional

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
        if self.path == "/" or self.path.startswith("/index"):
            html = (
                "<html><head><title>Vibe Setup</title></head>"
                "<body><h1>Vibe Setup</h1>"
                "<p>Setup UI is starting. Use /status for runtime status.</p>"
                "</body></html>"
            )
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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
        self._send_json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:
        if self.path == "/control":
            payload = self._read_json()
            action = payload.get("action")
            status_path = paths.get_runtime_status_path()
            status = {}
            if status_path.exists():
                status = json.loads(status_path.read_text(encoding="utf-8"))
            status["last_action"] = action
            status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
            self._send_json({"ok": True, "action": action})
            return
        self._send_json({"error": "not_found"}, status=404)


def run_ui_server(port: int) -> None:
    paths.ensure_data_dirs()
    with socketserver.TCPServer(("127.0.0.1", port), UiHandler) as httpd:
        httpd.serve_forever()
