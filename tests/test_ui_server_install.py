from __future__ import annotations

from config.v2_config import AgentsConfig, RuntimeConfig, SlackConfig, UiConfig, V2Config
from vibe import api
from vibe.ui_server import app

from tests.ui_server_test_helpers import csrf_headers


def _save_setup_host_config(host: str) -> None:
    V2Config(
        mode="self_host",
        version="v2",
        slack=SlackConfig(bot_token=""),
        runtime=RuntimeConfig(default_cwd="."),
        agents=AgentsConfig(),
        ui=UiConfig(setup_host=host),
    ).save()


def test_install_agent_allows_same_origin_request(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_setup_host_config("192.168.2.3")
    monkeypatch.setattr(
        api,
        "start_agent_install_job",
        lambda name: {"ok": True, "job_id": "job-1", "backend": name, "status": "running"},
    )

    client = app.test_client()
    response = client.post(
        "/agent/claude/install",
        headers=csrf_headers(client, "http://192.168.2.3:15131"),
        base_url="http://192.168.2.3:15131",
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert response.get_json()["status"] == "running"


def test_install_agent_rejects_cross_origin_request(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_setup_host_config("192.168.2.3")
    monkeypatch.setattr(api, "start_agent_install_job", lambda name: {"ok": True, "name": name})

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


def test_install_agent_rejects_missing_csrf_token(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    monkeypatch.setattr(api, "start_agent_install_job", lambda name: {"ok": True, "name": name})

    client = app.test_client()
    response = client.post(
        "/agent/codex/install",
        headers={"Origin": "http://127.0.0.1:15131"},
        base_url="http://127.0.0.1:15131",
    )

    assert response.status_code == 403
    assert response.get_json()["message"] == "Forbidden: invalid csrf token"


def test_install_agent_rejects_missing_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    monkeypatch.setattr(api, "start_agent_install_job", lambda name: {"ok": True, "name": name})

    client = app.test_client()
    response = client.post(
        "/agent/codex/install",
        headers={"X-Vibe-CSRF-Token": csrf_headers(client)["X-Vibe-CSRF-Token"]},
    )

    assert response.status_code == 403
    assert response.get_json()["message"] == "Forbidden: missing origin header"


def test_install_agent_status_allows_poll(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_setup_host_config("192.168.2.3")
    monkeypatch.setattr(
        api,
        "get_agent_install_job",
        lambda job_id, backend=None: {
            "ok": True,
            "job_id": job_id,
            "backend": backend,
            "status": "succeeded",
            "path": "/usr/local/bin/claude",
        },
    )

    client = app.test_client()
    response = client.get(
        "/agent/claude/install/job-1",
        headers=csrf_headers(client, "http://192.168.2.3:15131"),
        base_url="http://192.168.2.3:15131",
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "succeeded"
