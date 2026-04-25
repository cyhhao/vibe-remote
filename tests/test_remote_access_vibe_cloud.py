from __future__ import annotations

import json

from config.v2_config import AgentsConfig, PlatformsConfig, RemoteAccessConfig, RuntimeConfig, SlackConfig, UiConfig, V2Config
from config import paths
from vibe import remote_access
from vibe import runtime


def _config() -> V2Config:
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
    cloud.instance_id = "inst_123"
    cloud.client_id = "vr_client_123"
    cloud.public_url = "https://alex.vibe.io"
    cloud.session_secret = "session-secret"
    return config


def test_session_cookie_roundtrip() -> None:
    config = _config()

    cookie = remote_access.make_session_cookie(config, "alex@example.com", "user-1")

    assert remote_access.validate_session_cookie(config, cookie) is True
    assert remote_access.validate_session_cookie(config, cookie + "x") is False


def test_pair_redeems_key_and_starts_connector(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _config()
    config.remote_access.vibe_cloud.enabled = False
    config.remote_access.vibe_cloud.session_secret = ""
    config.save()

    def fake_request(url: str, payload: dict, timeout: float = 20.0):
        assert url == "https://backend.test/api/v1/pairing/redeem"
        assert payload["pairing_key"] == "vrp_test"
        return {
            "instance_id": "inst_123",
            "client_id": "vr_client_123",
            "issuer": "https://backend.test",
            "authorization_endpoint": "https://backend.test/oauth/authorize",
            "token_endpoint": "https://backend.test/oauth/token",
            "jwks_uri": "https://backend.test/oauth/jwks.json",
            "public_url": "https://alex.vibe.io",
            "redirect_uri": "https://alex.vibe.io/auth/callback",
            "tunnel_token": "tunnel-token",
            "instance_secret": "instance-secret",
        }

    monkeypatch.setattr(remote_access, "_json_request", fake_request)
    monkeypatch.setattr(remote_access, "start", lambda next_config: {"ok": True, "running": True})
    monkeypatch.setattr(remote_access, "status", lambda next_config=None: {"ok": True, "running": True, "paired": True})

    result = remote_access.pair("vrp_test", "https://backend.test")
    saved_payload = json.loads((tmp_path / "config" / "config.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert saved_payload["remote_access"]["vibe_cloud"]["enabled"] is True
    assert saved_payload["remote_access"]["vibe_cloud"]["tunnel_token"] == "tunnel-token"
    assert saved_payload["remote_access"]["vibe_cloud"]["session_secret"]


def test_pair_returns_structured_error_when_backend_request_fails(monkeypatch) -> None:
    monkeypatch.setattr(remote_access, "_json_request", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))

    result = remote_access.pair("vrp_test", "https://backend.test")

    assert result["ok"] is False
    assert result["error"] == "pairing_request_failed"
    assert "offline" in result["detail"]


def test_stop_ui_continues_when_remote_access_stop_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    stop_calls = []

    monkeypatch.setattr(remote_access, "stop", lambda: {"ok": False, "error": "cloudflared_stop_failed"})
    monkeypatch.setattr(runtime, "stop_process", lambda pid_path: stop_calls.append(pid_path) or True)

    assert runtime.stop_ui() is False
    assert stop_calls == [paths.get_runtime_ui_pid_path()]
