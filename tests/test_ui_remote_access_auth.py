from __future__ import annotations

from config.v2_config import AgentsConfig, PlatformsConfig, RemoteAccessConfig, RuntimeConfig, SlackConfig, UiConfig, V2Config
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
    cloud.public_url = "https://alex.vibe.io"
    cloud.client_id = "vr_client_123"
    cloud.instance_id = "inst_123"
    cloud.session_secret = "session-secret"
    cloud.authorization_endpoint = "https://backend.test/oauth/authorize"
    cloud.redirect_uri = "https://alex.vibe.io/auth/callback"
    config.save()
    return config


def test_remote_host_redirects_to_vibe_cloud_login(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get("/dashboard", base_url="https://alex.vibe.io", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://backend.test/oauth/authorize?")


def test_remote_host_with_explicit_port_still_requires_login(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get("/dashboard", base_url="https://alex.vibe.io:443", follow_redirects=False)

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
    client.set_cookie(remote_access.SESSION_COOKIE_NAME, remote_access.make_session_cookie(config, "alex@example.com", "user-1"), domain="alex.vibe.io")

    response = client.get("/dashboard", base_url="https://alex.vibe.io", follow_redirects=False)

    assert response.status_code != 302


def test_remote_host_fails_closed_when_config_load_fails(monkeypatch):
    def fail_load():
        raise ValueError("corrupt config")

    monkeypatch.setattr(ui_server.V2Config, "load", fail_load)

    response = app.test_client().get("/dashboard", base_url="https://alex.vibe.io", follow_redirects=False)

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_config_unavailable"
