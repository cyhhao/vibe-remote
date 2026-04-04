from __future__ import annotations

from vibe import api
from vibe.ui_server import app

from tests.ui_server_test_helpers import csrf_headers


def test_install_agent_allows_same_origin_request(monkeypatch):
    monkeypatch.setattr(api, "install_agent", lambda name: {"ok": True, "name": name, "path": "/usr/local/bin/claude"})

    client = app.test_client()
    response = client.post(
        "/agent/claude/install",
        headers=csrf_headers(client, "http://192.168.2.3:15131"),
        base_url="http://192.168.2.3:15131",
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_install_agent_rejects_cross_origin_request(monkeypatch):
    monkeypatch.setattr(api, "install_agent", lambda name: {"ok": True, "name": name})

    client = app.test_client()
    headers = csrf_headers(client, "http://192.168.2.3:15131")
    headers["Origin"] = "http://evil.example"
    response = client.post(
        "/agent/claude/install",
        headers=headers,
        base_url="http://192.168.2.3:15131",
    )

    assert response.status_code == 403
    assert response.get_json()["message"] == "Forbidden: invalid origin"


def test_install_agent_rejects_missing_csrf_token(monkeypatch):
    monkeypatch.setattr(api, "install_agent", lambda name: {"ok": True, "name": name})

    client = app.test_client()
    response = client.post(
        "/agent/codex/install",
        headers={"Origin": "http://127.0.0.1:15131"},
        base_url="http://127.0.0.1:15131",
    )

    assert response.status_code == 403
    assert response.get_json()["message"] == "Forbidden: invalid csrf token"


def test_install_agent_rejects_missing_origin(monkeypatch):
    monkeypatch.setattr(api, "install_agent", lambda name: {"ok": True, "name": name})

    client = app.test_client()
    response = client.post(
        "/agent/codex/install",
        headers={"X-Vibe-CSRF-Token": csrf_headers(client)["X-Vibe-CSRF-Token"]},
    )

    assert response.status_code == 403
    assert response.get_json()["message"] == "Forbidden: missing origin header"
