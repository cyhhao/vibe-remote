from __future__ import annotations

from http.cookies import SimpleCookie

from vibe.ui_server import app, protect_mutating_ui_requests

from tests.ui_server_test_helpers import csrf_headers


def test_csrf_token_endpoint_returns_cookie_and_token():
    client = app.test_client()
    response = client.get("/api/csrf-token", base_url="http://127.0.0.1:15131")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert isinstance(payload["csrf_token"], str)
    assert payload["csrf_token"]
    cookie_header = response.headers.get("Set-Cookie", "")
    assert "vibe_csrf_token=" in cookie_header
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    assert cookie["vibe_csrf_token"].value == payload["csrf_token"]


def test_config_post_rejects_cross_origin():
    client = app.test_client()
    headers = csrf_headers(client, "http://127.0.0.1:15131")
    headers["Origin"] = "http://evil.example"

    response = client.post(
        "/config",
        json={"mode": "self_host"},
        headers=headers,
        base_url="http://127.0.0.1:15131",
    )

    assert response.status_code == 403
    assert response.get_json()["message"] == "Forbidden: invalid origin"


def test_config_post_rejects_missing_csrf_token():
    client = app.test_client()
    response = client.post(
        "/config",
        json={"mode": "self_host"},
        headers={"Origin": "http://127.0.0.1:15131"},
        base_url="http://127.0.0.1:15131",
    )

    assert response.status_code == 403
    assert response.get_json()["message"] == "Forbidden: invalid csrf token"


def test_config_post_allows_forwarded_origin():
    client = app.test_client()
    headers = csrf_headers(client, "http://internal:15131")
    headers["Origin"] = "https://vibe.example"
    headers["X-Forwarded-Proto"] = "https"
    headers["X-Forwarded-Host"] = "vibe.example"

    response = client.post(
        "/config",
        json={
            "mode": "self_host",
            "runtime": {"default_cwd": "/tmp/test"},
            "agents": {
                "default_backend": "opencode",
                "opencode": {"enabled": True, "cli_path": "opencode"},
                "claude": {"enabled": False, "cli_path": "claude"},
                "codex": {"enabled": False, "cli_path": "codex"},
            },
        },
        headers=headers,
        base_url="http://internal:15131",
    )

    assert response.status_code == 200


def test_mutation_guard_exempts_e2e_simulation_endpoint(monkeypatch):
    monkeypatch.setenv("E2E_TEST_MODE", "true")
    with app.test_request_context("/e2e/simulate-interaction", method="POST"):
        assert protect_mutating_ui_requests() is None
