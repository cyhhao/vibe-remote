from __future__ import annotations

from config.v2_config import (
    AgentsConfig,
    RuntimeConfig,
    SlackConfig,
    UiConfig,
    V2Config,
)
from vibe import api
from vibe.ui_server import app


def test_install_agent_allows_configured_private_setup_host(monkeypatch):
    monkeypatch.setattr(api, "load_config", lambda: {"ui": {"setup_host": "192.168.2.3"}})
    monkeypatch.setattr(api, "install_agent", lambda name: {"ok": True, "name": name, "path": "/usr/local/bin/claude"})

    client = app.test_client()
    response = client.post(
        "/agent/claude/install",
        headers={"Origin": "http://192.168.2.3:15131"},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_install_agent_rejects_untrusted_origin(monkeypatch):
    monkeypatch.setattr(api, "load_config", lambda: {"ui": {"setup_host": "192.168.2.3"}})
    monkeypatch.setattr(api, "install_agent", lambda name: {"ok": True, "name": name})

    client = app.test_client()
    response = client.post(
        "/agent/claude/install",
        headers={"Origin": "http://evil.example"},
    )

    assert response.status_code == 403
    assert response.get_json()["message"] == "Forbidden: invalid origin"


def test_install_agent_allows_private_trusted_origin_with_v2_config_object(monkeypatch):
    monkeypatch.setattr(
        api,
        "load_config",
        lambda: V2Config(
            mode="self_host",
            version="v2",
            slack=SlackConfig(),
            runtime=RuntimeConfig(default_cwd="/tmp"),
            agents=AgentsConfig(),
            ui=UiConfig(setup_host="192.168.2.3", setup_port=5123, open_browser=False),
        ),
    )
    monkeypatch.setattr(api, "install_agent", lambda name: {"ok": True, "name": name})

    client = app.test_client()
    response = client.post(
        "/agent/claude/install",
        headers={"Origin": "http://192.168.2.3:15131"},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_install_agent_rejects_configured_public_setup_host(monkeypatch):
    monkeypatch.setattr(api, "load_config", lambda: {"ui": {"setup_host": "example.com"}})
    monkeypatch.setattr(api, "install_agent", lambda name: {"ok": True, "name": name})

    client = app.test_client()
    response = client.post(
        "/agent/codex/install",
        headers={"Origin": "http://example.com"},
    )

    assert response.status_code == 403
    assert response.get_json()["message"] == "Forbidden: local access only"
