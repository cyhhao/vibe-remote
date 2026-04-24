from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_config import AgentsConfig, RuntimeConfig, SlackConfig, V2Config
from vibe import remote_access, runtime


def _config(*, enabled: bool = True, tunnel_token: str = "token-1", cloudflared_path: str = "") -> V2Config:
    config = V2Config(
        mode="self_host",
        version="v2",
        platform="slack",
        slack=SlackConfig(),
        runtime=RuntimeConfig(default_cwd="/tmp/workdir"),
        agents=AgentsConfig(),
    )
    config.remote_access.cloudflare.enabled = enabled
    config.remote_access.cloudflare.tunnel_token = tunnel_token
    config.remote_access.cloudflare.cloudflared_path = cloudflared_path
    return config


def test_start_cloudflare_preserves_failed_status(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()

    missing_token = remote_access.start_cloudflare(_config(tunnel_token=""))
    assert missing_token["ok"] is False
    assert missing_token["error"] == "missing_tunnel_token"

    monkeypatch.setattr(remote_access, "_resolve_configured_binary", lambda config=None: None)
    missing_binary = remote_access.start_cloudflare(_config(tunnel_token="token-1"))
    assert missing_binary["ok"] is False
    assert missing_binary["error"] == "cloudflared_not_installed"


def test_start_cloudflare_restarts_when_runtime_signature_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()

    next_pid = 4100
    live_pids: set[int] = set()
    stopped_pids: list[int] = []

    def fake_spawn_background(args, pid_path, stdout_name, stderr_name, env=None):
        nonlocal next_pid
        next_pid += 1
        live_pids.add(next_pid)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(next_pid), encoding="utf-8")
        return next_pid

    def fake_stop_process(pid_path, timeout=5):
        pid = int(pid_path.read_text(encoding="utf-8"))
        stopped_pids.append(pid)
        live_pids.discard(pid)
        pid_path.unlink(missing_ok=True)
        return True

    monkeypatch.setattr(remote_access, "_resolve_configured_binary", lambda config=None: "/bin/cloudflared")
    monkeypatch.setattr(remote_access, "_version", lambda path: None)
    monkeypatch.setattr(runtime, "spawn_background", fake_spawn_background)
    monkeypatch.setattr(runtime, "stop_process", fake_stop_process)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid in live_pids)
    monkeypatch.setattr(runtime, "get_process_command", lambda pid: "cloudflared tunnel run")

    first = remote_access.start_cloudflare(_config(tunnel_token="token-1"))
    second = remote_access.start_cloudflare(_config(tunnel_token="token-2"))

    assert first["ok"] is True
    assert first["started"] is True
    assert second["ok"] is True
    assert second["restarted"] is True
    assert second["pid"] != first["pid"]
    assert stopped_pids == [first["pid"]]


def test_stop_cloudflare_does_not_stop_reused_unrelated_pid(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()

    pid = 4321
    remote_access._pid_path().parent.mkdir(parents=True, exist_ok=True)
    remote_access._pid_path().write_text(str(pid), encoding="utf-8")
    stop_calls: list[int] = []

    def fake_stop_process(pid_path, timeout=5):
        stop_calls.append(int(pid_path.read_text(encoding="utf-8")))
        return True

    monkeypatch.setattr(runtime, "pid_alive", lambda candidate: candidate == pid)
    monkeypatch.setattr(runtime, "get_process_command", lambda candidate: "/usr/bin/python unrelated.py")
    monkeypatch.setattr(runtime, "stop_process", fake_stop_process)

    result = remote_access.stop_cloudflare()

    assert result["ok"] is True
    assert result["stopped"] is False
    assert result["stale_pid"] is True
    assert stop_calls == []
    assert not remote_access._pid_path().exists()


def test_cloudflared_command_detection_accepts_quoted_paths_with_spaces():
    assert remote_access._is_cloudflared_command(
        '"C:\\Users\\John Doe\\.vibe_remote\\bin\\cloudflared.exe" tunnel --no-autoupdate run'
    )
    assert remote_access._is_cloudflared_command(
        '"/Users/alex/Application Support/Vibe Remote/bin/cloudflared" tunnel run'
    )
    assert not remote_access._is_cloudflared_command(
        '"C:\\Users\\John Doe\\bin\\python.exe" unrelated.py'
    )
