from __future__ import annotations

from config.v2_config import AgentsConfig, PlatformsConfig, RemoteAccessConfig, RuntimeConfig, SlackConfig, UiConfig, V2Config
from tests.ui_server_test_helpers import csrf_headers
from vibe import api
from vibe import remote_access
from vibe import ui_server
from vibe.ui_server import app


def _save_config(tmp_path) -> V2Config:
    config = V2Config(
        mode="self_host",
        version="v2",
        platform="slack",
        platforms=PlatformsConfig(enabled=["slack"], primary="slack"),
        slack=SlackConfig(bot_token=""),
        runtime=RuntimeConfig(default_cwd="."),
        agents=AgentsConfig(),
        ui=UiConfig(),
        remote_access=RemoteAccessConfig(),
    )
    cloud = config.remote_access.vibe_cloud
    cloud.enabled = True
    cloud.public_url = "https://alex.avibe.bot"
    cloud.client_id = "vr_client_123"
    cloud.instance_id = "inst_123"
    cloud.session_secret = "session-secret"
    cloud.authorization_endpoint = "https://backend.test/oauth/authorize"
    cloud.redirect_uri = "https://alex.avibe.bot/auth/callback"
    config.save()
    return config


def test_remote_host_redirects_to_vibe_cloud_login(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get("/dashboard", base_url="https://alex.avibe.bot", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://backend.test/oauth/authorize?")


def test_remote_host_with_explicit_port_still_requires_login(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get("/dashboard", base_url="https://alex.avibe.bot:443", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://backend.test/oauth/authorize?")


def test_remote_host_with_trailing_dot_still_requires_login(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get("/dashboard", base_url="https://alex.avibe.bot.", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://backend.test/oauth/authorize?")


def test_localhost_does_not_require_remote_access_cookie(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get("/health", base_url="http://127.0.0.1:5123")

    assert response.status_code == 200


def test_remote_host_allows_valid_remote_session(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    client = app.test_client()
    client.set_cookie(remote_access.SESSION_COOKIE_NAME, remote_access.make_session_cookie(config, "alex@example.com", "user-1"), domain="alex.avibe.bot")

    response = client.get("/dashboard", base_url="https://alex.avibe.bot", follow_redirects=False)

    assert response.status_code != 302


def test_remote_host_fails_closed_when_config_load_fails(monkeypatch):
    def fail_load():
        raise ValueError("corrupt config")

    monkeypatch.setattr(ui_server.V2Config, "load", fail_load)

    response = app.test_client().get("/dashboard", base_url="https://alex.avibe.bot", follow_redirects=False)

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_config_unavailable"


def test_remote_host_fails_closed_when_disabled_but_hostname_still_matches(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.enabled = False
    config.save()

    response = app.test_client().get("/dashboard", base_url="https://alex.avibe.bot", follow_redirects=False)

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_disabled"


def test_remote_host_fails_closed_when_public_url_is_invalid(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.public_url = "alex.avibe.bot"
    config.save()

    response = app.test_client().get("/dashboard", base_url="https://alex.avibe.bot", follow_redirects=False)

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_public_url_invalid"


def test_remote_host_fails_closed_when_public_url_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.public_url = ""
    config.save()

    response = app.test_client().get("/dashboard", base_url="https://alex.avibe.bot", follow_redirects=False)

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_public_url_invalid"


def test_config_post_rotates_session_secret_when_remote_access_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    old_secret = config.remote_access.vibe_cloud.session_secret
    client = app.test_client()

    monkeypatch.setattr(remote_access, "reconcile", lambda next_config: {"ok": True, "stopped": True})

    response = client.post(
        "/config",
        json={"remote_access": {"vibe_cloud": {"enabled": False}}},
        headers=csrf_headers(client, "http://127.0.0.1:5123"),
        base_url="http://127.0.0.1:5123",
    )
    saved = V2Config.load()

    assert response.status_code == 200
    assert saved.remote_access.vibe_cloud.enabled is False
    assert saved.remote_access.vibe_cloud.session_secret
    assert saved.remote_access.vibe_cloud.session_secret != old_secret


def test_config_post_skips_reconcile_when_remote_access_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    client = app.test_client()
    reconcile_calls = []

    monkeypatch.setattr(remote_access, "reconcile", lambda next_config: reconcile_calls.append(next_config) or {"ok": True})

    response = client.post(
        "/config",
        json=api.config_to_payload(config),
        headers=csrf_headers(client, "http://127.0.0.1:5123"),
        base_url="http://127.0.0.1:5123",
    )

    assert response.status_code == 200
    assert reconcile_calls == []


def test_config_post_returns_saved_config_when_remote_reconcile_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    old_secret = config.remote_access.vibe_cloud.session_secret
    client = app.test_client()

    monkeypatch.setattr(remote_access, "reconcile", lambda next_config: {"ok": False, "error": "cloudflared_stop_failed"})

    response = client.post(
        "/config",
        json={"remote_access": {"vibe_cloud": {"enabled": False}}},
        headers=csrf_headers(client, "http://127.0.0.1:5123"),
        base_url="http://127.0.0.1:5123",
    )
    saved = V2Config.load()
    body = response.get_json()

    assert response.status_code == 200
    assert body["remote_access_runtime"]["ok"] is False
    assert body["remote_access_runtime"]["error"] == "cloudflared_stop_failed"
    assert saved.remote_access.vibe_cloud.enabled is False
    assert saved.remote_access.vibe_cloud.session_secret != old_secret


def test_remote_callback_rejects_nonce_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    client = app.test_client()

    with app.test_request_context("/dashboard", base_url="https://alex.avibe.bot"):
        redirect = ui_server._redirect_to_vibe_cloud_login(config)
    oauth_cookie = redirect.headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]
    client.set_cookie(ui_server.REMOTE_OAUTH_COOKIE_NAME, oauth_cookie, domain="alex.avibe.bot")

    monkeypatch.setattr(
        remote_access,
        "exchange_oauth_code",
        lambda cfg, code, verifier: {
            "claims": {
                "email": "alex@example.com",
                "sub": "user-1",
                "nonce": "wrong-nonce",
            }
        },
    )

    state = ui_server._read_oauth_cookie(config.remote_access.vibe_cloud.session_secret, oauth_cookie)["state"]
    response = client.get(f"/auth/callback?code=test-code&state={state}", base_url="https://alex.avibe.bot")

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_oauth_nonce"


def test_remote_callback_sanitizes_protocol_relative_next(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    client = app.test_client()
    oauth_cookie = ui_server._make_oauth_cookie(
        config.remote_access.vibe_cloud.session_secret,
        {
            "state": "state-1",
            "nonce": "nonce-1",
            "code_verifier": "verifier-1",
            "next": "//attacker.example",
            "exp": int(ui_server.datetime.now().timestamp()) + 300,
        },
    )
    client.set_cookie(ui_server.REMOTE_OAUTH_COOKIE_NAME, oauth_cookie, domain="alex.avibe.bot")

    monkeypatch.setattr(
        remote_access,
        "exchange_oauth_code",
        lambda cfg, code, verifier: {
            "claims": {
                "email": "alex@example.com",
                "sub": "user-1",
                "nonce": "nonce-1",
            }
        },
    )

    response = client.get("/auth/callback?code=test-code&state=state-1", base_url="https://alex.avibe.bot")

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
