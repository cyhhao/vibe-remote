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
    cloud.public_url = "https://alex.avibe.bot"
    cloud.session_secret = "session-secret"
    return config


def test_session_cookie_roundtrip() -> None:
    config = _config()

    cookie = remote_access.make_session_cookie(config, "alex@example.com", "user-1")

    assert remote_access.validate_session_cookie(config, cookie) is True
    assert remote_access.validate_session_cookie(config, cookie + "x") is False


def test_session_cookie_rejects_empty_session_secret() -> None:
    config = _config()
    config.remote_access.vibe_cloud.session_secret = ""

    assert remote_access.validate_session_cookie(config, "payload.signature") is False


def test_make_session_cookie_requires_session_secret() -> None:
    config = _config()
    config.remote_access.vibe_cloud.session_secret = ""

    import pytest

    with pytest.raises(ValueError, match="session secret"):
        remote_access.make_session_cookie(config, "alex@example.com", "user-1")


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
            "public_url": "https://alex.avibe.bot",
            "redirect_uri": "https://alex.avibe.bot/auth/callback",
            "tunnel_token": "tunnel-token",
            "instance_secret": "instance-secret",
        }

    monkeypatch.setattr(remote_access, "_json_request", fake_request)
    monkeypatch.setattr(remote_access, "start", lambda next_config: {"ok": True, "running": True})
    monkeypatch.setattr(remote_access, "status", lambda next_config=None: {"ok": True, "running": True, "paired": True})

    result = remote_access.pair("vrp_test", "https://backend.test")
    saved_payload = json.loads((tmp_path / "config" / "config.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["pairing"]["ok"] is True
    assert result["start"]["ok"] is True
    assert saved_payload["remote_access"]["vibe_cloud"]["enabled"] is True
    assert saved_payload["remote_access"]["vibe_cloud"]["tunnel_token"] == "tunnel-token"
    assert saved_payload["remote_access"]["vibe_cloud"]["session_secret"]


def test_pair_persists_with_locked_incremental_config_save(monkeypatch) -> None:
    config = _config()
    save_payloads = []

    monkeypatch.setattr(
        remote_access,
        "_json_request",
        lambda *args, **kwargs: {
            "instance_id": "inst_123",
            "client_id": "vr_client_123",
            "issuer": "https://backend.test",
            "authorization_endpoint": "https://backend.test/oauth/authorize",
            "token_endpoint": "https://backend.test/oauth/token",
            "jwks_uri": "https://backend.test/oauth/jwks.json",
            "public_url": "https://alex.avibe.bot",
            "redirect_uri": "https://alex.avibe.bot/auth/callback",
            "tunnel_token": "tunnel-token",
            "instance_secret": "instance-secret",
        },
    )
    monkeypatch.setattr(remote_access.api, "save_config", lambda payload: save_payloads.append(payload) or config)
    monkeypatch.setattr(remote_access, "start", lambda next_config: {"ok": True, "running": True})
    monkeypatch.setattr(remote_access, "status", lambda next_config=None: {"ok": True, "running": True, "paired": True})

    result = remote_access.pair("vrp_test", "https://backend.test")

    assert result["ok"] is True
    assert save_payloads
    assert set(save_payloads[0]) == {"remote_access"}
    cloud_payload = save_payloads[0]["remote_access"]["vibe_cloud"]
    assert cloud_payload["enabled"] is True
    assert cloud_payload["tunnel_token"] == "tunnel-token"
    assert cloud_payload["session_secret"]


def test_pair_reports_success_when_connector_start_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _config()
    config.remote_access.vibe_cloud.enabled = False
    config.save()

    monkeypatch.setattr(
        remote_access,
        "_json_request",
        lambda *args, **kwargs: {
            "instance_id": "inst_123",
            "client_id": "vr_client_123",
            "issuer": "https://backend.test",
            "authorization_endpoint": "https://backend.test/oauth/authorize",
            "token_endpoint": "https://backend.test/oauth/token",
            "jwks_uri": "https://backend.test/oauth/jwks.json",
            "public_url": "https://alex.avibe.bot",
            "redirect_uri": "https://alex.avibe.bot/auth/callback",
            "tunnel_token": "tunnel-token",
            "instance_secret": "instance-secret",
        },
    )
    monkeypatch.setattr(remote_access, "start", lambda next_config: {"ok": False, "error": "cloudflared_spawn_failed"})
    monkeypatch.setattr(remote_access, "status", lambda next_config=None: {"ok": True, "running": False, "paired": True})

    result = remote_access.pair("vrp_test", "https://backend.test")
    saved_payload = json.loads((tmp_path / "config" / "config.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["pairing"]["ok"] is True
    assert result["start"]["ok"] is False
    assert result["start"]["error"] == "cloudflared_spawn_failed"
    assert saved_payload["remote_access"]["vibe_cloud"]["tunnel_token"] == "tunnel-token"


def test_pair_returns_structured_error_when_backend_request_fails(monkeypatch) -> None:
    monkeypatch.setattr(remote_access, "_json_request", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))

    result = remote_access.pair("vrp_test", "https://backend.test")

    assert result["ok"] is False
    assert result["error"] == "pairing_request_failed"
    assert "offline" in result["detail"]


def test_pair_preserves_backend_error_response(monkeypatch) -> None:
    def fake_request(*args, **kwargs):
        raise remote_access.BackendRequestError(400, {"error": "invalid_pairing_key"})

    monkeypatch.setattr(remote_access, "_json_request", fake_request)

    result = remote_access.pair("vrp_test", "https://backend.test")

    assert result == {"ok": False, "error": "invalid_pairing_key", "status": 400}


def test_stop_ui_continues_when_remote_access_stop_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    stop_calls = []

    monkeypatch.setattr(remote_access, "stop", lambda: {"ok": False, "error": "cloudflared_stop_failed"})
    monkeypatch.setattr(runtime, "stop_process", lambda pid_path: stop_calls.append(pid_path) or True)

    assert runtime.stop_ui() is False
    assert stop_calls == [paths.get_runtime_ui_pid_path()]


def test_cloudflared_pid_detection_handles_quoted_paths_with_spaces(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid == 123)
    monkeypatch.setattr(
        runtime,
        "get_process_command",
        lambda pid: '"C:\\Program Files\\Cloudflare\\cloudflared.exe" tunnel --no-autoupdate run',
    )

    assert remote_access._is_cloudflared_pid(123) is True


def test_stop_preserves_pid_file_when_process_stop_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    pid = 123
    remote_access._pid_path().parent.mkdir(parents=True, exist_ok=True)
    remote_access._pid_path().write_text(str(pid), encoding="utf-8")
    remote_access._state_path().write_text('{"pid": 123}', encoding="utf-8")

    monkeypatch.setattr(runtime, "pid_alive", lambda candidate: candidate == pid)
    monkeypatch.setattr(runtime, "get_process_command", lambda candidate: "cloudflared tunnel run")
    monkeypatch.setattr(runtime, "stop_pid", lambda candidate, timeout=8: False)

    result = remote_access.stop()

    assert result["ok"] is False
    assert result["error"] == "cloudflared_stop_failed"
    assert remote_access._pid_path().read_text(encoding="utf-8") == str(pid)
    assert remote_access._state_path().exists()


def test_status_preserves_pid_file_when_process_command_is_unknown(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    pid = 123
    remote_access._pid_path().parent.mkdir(parents=True, exist_ok=True)
    remote_access._pid_path().write_text(str(pid), encoding="utf-8")
    remote_access._state_path().write_text('{"pid": 123}', encoding="utf-8")

    monkeypatch.setattr(runtime, "pid_alive", lambda candidate: candidate == pid)
    monkeypatch.setattr(runtime, "get_process_command", lambda candidate: None)

    result = remote_access.status(_config())

    assert result["running"] is False
    assert result["pid"] == pid
    assert result["pid_state"] == "unknown"
    assert remote_access._pid_path().read_text(encoding="utf-8") == str(pid)
    assert remote_access._state_path().exists()


def test_start_refuses_duplicate_connector_when_process_command_is_unknown(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    pid = 123
    config = _config()
    config.remote_access.vibe_cloud.tunnel_token = "tunnel-token"
    remote_access._pid_path().parent.mkdir(parents=True, exist_ok=True)
    remote_access._pid_path().write_text(str(pid), encoding="utf-8")
    remote_access._state_path().write_text('{"pid": 123}', encoding="utf-8")
    spawn_calls = []

    monkeypatch.setattr(runtime, "pid_alive", lambda candidate: candidate == pid)
    monkeypatch.setattr(runtime, "get_process_command", lambda candidate: None)
    monkeypatch.setattr(remote_access, "_resolve_binary", lambda cfg: "/usr/local/bin/cloudflared")
    monkeypatch.setattr(remote_access, "_version", lambda path: "cloudflared test")
    monkeypatch.setattr(runtime, "spawn_background", lambda *args, **kwargs: spawn_calls.append(args) or 456)

    result = remote_access.start(config)

    assert result["ok"] is False
    assert result["error"] == "cloudflared_process_unknown"
    assert spawn_calls == []
    assert remote_access._pid_path().read_text(encoding="utf-8") == str(pid)
    assert remote_access._state_path().exists()


def test_start_returns_failure_when_remote_access_is_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _config()
    config.remote_access.vibe_cloud.enabled = False

    monkeypatch.setattr(remote_access, "stop", lambda config=None: {"ok": True, "stopped": False})

    result = remote_access.start(config)

    assert result["ok"] is False
    assert result["error"] == "remote_access_disabled"


def test_start_loads_config_before_connector_lock(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _config()
    config.remote_access.vibe_cloud.enabled = False
    load_lock_states = []

    def load_config():
        load_lock_states.append(remote_access._CONNECTOR_LOCK._is_owned())
        return config

    monkeypatch.setattr(remote_access.V2Config, "load", load_config)
    monkeypatch.setattr(remote_access, "stop", lambda loaded_config=None: {"ok": True, "stopped": False})

    result = remote_access.start()

    assert result["ok"] is False
    assert result["error"] == "remote_access_disabled"
    assert load_lock_states == [False]


def test_stop_loads_config_before_connector_lock(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _config()
    load_lock_states = []

    def load_config():
        load_lock_states.append(remote_access._CONNECTOR_LOCK._is_owned())
        return config

    monkeypatch.setattr(remote_access.V2Config, "load", load_config)

    result = remote_access.stop()

    assert result["ok"] is True
    assert load_lock_states == [False]


def test_reconcile_stops_when_remote_access_is_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _config()
    config.remote_access.vibe_cloud.enabled = False

    monkeypatch.setattr(remote_access, "stop", lambda next_config=None: {"ok": True, "stopped": True})

    result = remote_access.reconcile(config)

    assert result == {"ok": True, "stopped": True}


def test_start_restarts_when_runtime_signature_changes(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _config()
    config.remote_access.vibe_cloud.tunnel_token = "new-token"
    binary = "/usr/local/bin/cloudflared"
    old_pid = 111
    new_pid = 222
    remote_access._pid_path().parent.mkdir(parents=True, exist_ok=True)
    remote_access._pid_path().write_text(str(old_pid), encoding="utf-8")
    remote_access._state_path().write_text(
        json.dumps(
            {
                "pid": old_pid,
                "provider": "vibe_cloud",
                "binary_path": binary,
                "public_url": "https://alex.avibe.bot",
                "tunnel_token_sha256": "old-token-hash",
            }
        ),
        encoding="utf-8",
    )
    alive = {old_pid, new_pid}

    monkeypatch.setattr(remote_access, "_resolve_binary", lambda cfg: binary)
    monkeypatch.setattr(remote_access, "_version", lambda path: "cloudflared test")
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(runtime, "get_process_command", lambda pid: f"{binary} tunnel run")

    def stop_pid(pid, timeout=8):
        alive.discard(pid)
        return True

    monkeypatch.setattr(runtime, "stop_pid", stop_pid)
    def spawn_background(args, pid_path, stdout_name, stderr_name, env=None):
        pid_path.write_text(str(new_pid), encoding="utf-8")
        return new_pid

    monkeypatch.setattr(runtime, "spawn_background", spawn_background)

    result = remote_access.start(config)
    state = json.loads(remote_access._state_path().read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["started"] is True
    assert result["pid"] == new_pid
    assert old_pid not in alive
    assert state["tunnel_token_sha256"] == "348e9df2a42bd6e3c6356ca9c95c5f1fe9a6b3e5cd25f4ae58df0f09049c3209"
