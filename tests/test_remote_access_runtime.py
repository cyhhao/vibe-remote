from __future__ import annotations

import io
import sys
import tarfile
import threading
from pathlib import Path

import pytest

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
    config.remote_access.cloudflare.hostname = "admin.example.com"
    config.remote_access.cloudflare.tunnel_token = tunnel_token
    config.remote_access.cloudflare.cloudflared_path = cloudflared_path
    config.remote_access.cloudflare.allowed_emails = ["alex@example.com"]
    config.remote_access.cloudflare.confirmed_access_policy = True
    config.remote_access.cloudflare.confirmed_tunnel_route = True
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


def test_start_cloudflare_stops_running_connector_when_tunnel_token_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()

    pid = 6100
    binary = "/bin/cloudflared"
    live_pids = {pid}
    stopped_pids: list[int] = []
    remote_access._pid_path().parent.mkdir(parents=True, exist_ok=True)
    remote_access._pid_path().write_text(str(pid), encoding="utf-8")
    remote_access._CONNECTOR_ENV[pid] = {
        "binary_path": binary,
        "tunnel_token_sha256": "token-hash",
    }

    def fake_stop_process(pid_path, timeout=5):
        stopped_pid = int(pid_path.read_text(encoding="utf-8"))
        stopped_pids.append(stopped_pid)
        live_pids.discard(stopped_pid)
        pid_path.unlink(missing_ok=True)
        return True

    monkeypatch.setattr(remote_access, "_resolve_configured_binary", lambda config=None: binary)
    monkeypatch.setattr(remote_access, "_version", lambda path: None)
    monkeypatch.setattr(runtime, "pid_alive", lambda candidate: candidate in live_pids)
    monkeypatch.setattr(runtime, "get_process_command", lambda candidate: f"{binary} tunnel run")
    monkeypatch.setattr(runtime, "stop_process", fake_stop_process)

    result = remote_access.start_cloudflare(_config(tunnel_token=""))

    assert result["ok"] is False
    assert result["error"] == "missing_tunnel_token"
    assert stopped_pids == [pid]
    assert not remote_access._pid_path().exists()
    assert pid not in remote_access._CONNECTOR_ENV


def test_start_cloudflare_requires_access_checklist_before_spawn(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()
    spawn_calls: list[list[str]] = []

    config = _config()
    config.remote_access.cloudflare.allowed_emails = []
    config.remote_access.cloudflare.allowed_email_domains = []

    monkeypatch.setattr(remote_access, "_resolve_configured_binary", lambda config=None: "/bin/cloudflared")
    monkeypatch.setattr(runtime, "spawn_background", lambda args, *rest, **kwargs: spawn_calls.append(args) or 6101)

    result = remote_access.start_cloudflare(config)

    assert result["ok"] is False
    assert result["error"] == "access_checklist_incomplete"
    assert spawn_calls == []


def test_start_cloudflare_rejects_blank_access_allow_list_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()
    spawn_calls: list[list[str]] = []

    config = _config()
    config.remote_access.cloudflare.allowed_emails = ["   "]
    config.remote_access.cloudflare.allowed_email_domains = [""]

    monkeypatch.setattr(remote_access, "_resolve_configured_binary", lambda config=None: "/bin/cloudflared")
    monkeypatch.setattr(runtime, "spawn_background", lambda args, *rest, **kwargs: spawn_calls.append(args) or 6104)

    result = remote_access.start_cloudflare(config)

    assert result["ok"] is False
    assert result["error"] == "access_checklist_incomplete"
    assert spawn_calls == []


def test_start_cloudflare_stops_running_connector_when_access_checklist_becomes_invalid(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()

    pid = 6102
    binary = "/bin/cloudflared"
    live_pids = {pid}
    stopped_pids: list[int] = []
    remote_access._pid_path().parent.mkdir(parents=True, exist_ok=True)
    remote_access._pid_path().write_text(str(pid), encoding="utf-8")
    remote_access._CONNECTOR_ENV[pid] = {
        "binary_path": binary,
        "tunnel_token_sha256": "token-hash",
    }
    config = _config()
    config.remote_access.cloudflare.confirmed_access_policy = False

    def fake_stop_process(pid_path, timeout=5):
        stopped_pid = int(pid_path.read_text(encoding="utf-8"))
        stopped_pids.append(stopped_pid)
        live_pids.discard(stopped_pid)
        pid_path.unlink(missing_ok=True)
        return True

    monkeypatch.setattr(remote_access, "_resolve_configured_binary", lambda config=None: binary)
    monkeypatch.setattr(remote_access, "_version", lambda path: None)
    monkeypatch.setattr(runtime, "pid_alive", lambda candidate: candidate in live_pids)
    monkeypatch.setattr(runtime, "get_process_command", lambda candidate: f"{binary} tunnel run")
    monkeypatch.setattr(runtime, "stop_process", fake_stop_process)

    result = remote_access.start_cloudflare(config)

    assert result["ok"] is False
    assert result["error"] == "access_checklist_incomplete"
    assert stopped_pids == [pid]
    assert not remote_access._pid_path().exists()
    assert pid not in remote_access._CONNECTOR_ENV


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


def test_start_cloudflare_serializes_concurrent_starts(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()

    binary = "/bin/cloudflared"
    live_pids: set[int] = set()
    spawn_calls: list[int] = []
    spawned = threading.Event()
    release_spawn = threading.Event()

    def fake_spawn_background(args, pid_path, stdout_name, stderr_name, env=None):
        pid = 4201 + len(spawn_calls)
        spawn_calls.append(pid)
        spawned.set()
        assert release_spawn.wait(timeout=2)
        live_pids.add(pid)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(pid), encoding="utf-8")
        return pid

    monkeypatch.setattr(remote_access, "_resolve_configured_binary", lambda config=None: binary)
    monkeypatch.setattr(remote_access, "_version", lambda path: None)
    monkeypatch.setattr(runtime, "spawn_background", fake_spawn_background)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid in live_pids)
    monkeypatch.setattr(runtime, "get_process_command", lambda pid: f"{binary} tunnel run")

    results: list[dict] = []
    first = threading.Thread(target=lambda: results.append(remote_access.start_cloudflare(_config())), daemon=True)
    second = threading.Thread(target=lambda: results.append(remote_access.start_cloudflare(_config())), daemon=True)

    first.start()
    assert spawned.wait(timeout=2)
    second.start()
    release_spawn.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert spawn_calls == [4201]
    assert len(results) == 2
    assert sum(1 for result in results if result.get("started") is True) == 1
    assert sum(1 for result in results if result.get("started") is False) == 1


def test_start_cloudflare_returns_error_when_restart_stop_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()

    pid = 4102
    binary = "/bin/cloudflared"
    remote_access._pid_path().parent.mkdir(parents=True, exist_ok=True)
    remote_access._pid_path().write_text(str(pid), encoding="utf-8")
    remote_access._CONNECTOR_ENV[pid] = {
        "binary_path": binary,
        "tunnel_token_sha256": "old-token-hash",
    }
    spawn_calls: list[list[str]] = []

    monkeypatch.setattr(remote_access, "_resolve_configured_binary", lambda config=None: binary)
    monkeypatch.setattr(remote_access, "_version", lambda path: None)
    monkeypatch.setattr(runtime, "pid_alive", lambda candidate: candidate == pid)
    monkeypatch.setattr(runtime, "get_process_command", lambda candidate: f"{binary} tunnel run")
    monkeypatch.setattr(runtime, "stop_process", lambda pid_path, timeout=5: False)
    monkeypatch.setattr(runtime, "spawn_background", lambda args, *rest, **kwargs: spawn_calls.append(args) or 4103)

    result = remote_access.start_cloudflare(_config(tunnel_token="token-2"))

    assert result["ok"] is False
    assert result["error"] == "cloudflared_stop_failed"
    assert result["restarted"] is False
    assert result["running"] is True
    assert result["pid"] == pid
    assert spawn_calls == []


def test_start_cloudflare_returns_structured_spawn_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()
    binary = "/missing/cloudflared"

    monkeypatch.setattr(remote_access, "_resolve_configured_binary", lambda config=None: binary)
    monkeypatch.setattr(remote_access, "_version", lambda path: None)
    monkeypatch.setattr(runtime, "spawn_background", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom")))

    result = remote_access.start_cloudflare(_config(cloudflared_path=binary))

    assert result["ok"] is False
    assert result["error"] == "cloudflared_spawn_failed"
    assert "boom" in result["detail"]
    assert not remote_access._pid_path().exists()


def test_start_cloudflare_accepts_configured_binary_with_custom_name(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()

    binary = str(tmp_path / "bin" / "vibe-cloudflare-connector")
    live_pids = {5101}

    def fake_spawn_background(args, pid_path, stdout_name, stderr_name, env=None):
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("5101", encoding="utf-8")
        return 5101

    monkeypatch.setattr(remote_access, "_resolve_configured_binary", lambda config=None: binary)
    monkeypatch.setattr(remote_access, "_version", lambda path: None)
    monkeypatch.setattr(runtime, "spawn_background", fake_spawn_background)
    monkeypatch.setattr(runtime, "pid_alive", lambda pid: pid in live_pids)
    monkeypatch.setattr(runtime, "get_process_command", lambda pid: f'"{binary}" tunnel --no-autoupdate run')

    result = remote_access.start_cloudflare(_config(tunnel_token="token-1", cloudflared_path=binary))

    assert result["ok"] is True
    assert result["running"] is True
    assert result["pid"] == 5101


def test_status_prefers_recorded_binary_when_config_path_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()

    pid = 5102
    old_binary = "/Applications/Vibe Remote/bin/renamed-connector"
    new_binary = "/Applications/Vibe Remote/bin/cloudflared-new"
    remote_access._pid_path().parent.mkdir(parents=True, exist_ok=True)
    remote_access._pid_path().write_text(str(pid), encoding="utf-8")
    remote_access._CONNECTOR_ENV[pid] = {
        "binary_path": old_binary,
        "tunnel_token_sha256": "token-hash",
    }

    monkeypatch.setattr(remote_access, "_resolve_configured_binary", lambda config=None: new_binary)
    monkeypatch.setattr(remote_access, "_version", lambda path: None)
    monkeypatch.setattr(runtime, "pid_alive", lambda candidate: candidate == pid)
    monkeypatch.setattr(runtime, "get_process_command", lambda candidate: f"{old_binary} tunnel --no-autoupdate run")

    result = remote_access.status(_config(cloudflared_path=new_binary))

    assert result["running"] is True
    assert result["pid"] == pid
    assert result["binary_path"] == new_binary


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


def test_stop_cloudflare_preserves_state_when_process_stop_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    remote_access._CONNECTOR_ENV.clear()

    pid = 4322
    binary = "/bin/cloudflared"
    remote_access._pid_path().parent.mkdir(parents=True, exist_ok=True)
    remote_access._pid_path().write_text(str(pid), encoding="utf-8")
    remote_access._CONNECTOR_ENV[pid] = {
        "binary_path": binary,
        "tunnel_token_sha256": "token-hash",
    }

    monkeypatch.setattr(runtime, "pid_alive", lambda candidate: candidate == pid)
    monkeypatch.setattr(runtime, "get_process_command", lambda candidate: f"{binary} tunnel run")
    monkeypatch.setattr(runtime, "stop_process", lambda pid_path, timeout=5: False)
    monkeypatch.setattr(remote_access, "_version", lambda path: None)

    result = remote_access.stop_cloudflare()

    assert result["ok"] is False
    assert result["error"] == "cloudflared_stop_failed"
    assert result["running"] is True
    assert result["pid"] == pid
    assert remote_access._pid_path().read_text(encoding="utf-8") == str(pid)
    assert remote_access._CONNECTOR_ENV[pid]["binary_path"] == binary


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


def test_cloudflared_command_detection_accepts_configured_custom_name():
    command = '"C:\\Users\\John Doe\\.vibe_remote\\bin\\renamed-connector.exe" tunnel run'
    configured = "C:\\Users\\John Doe\\.vibe_remote\\bin\\renamed-connector.exe"

    assert remote_access._is_cloudflared_command(command, configured)
    assert not remote_access._is_cloudflared_command(command)


def test_cloudflared_command_detection_accepts_unquoted_expected_path_with_spaces():
    command = "/Users/alex/Application Support/Vibe Remote/bin/renamed connector tunnel run"
    configured = "/Users/alex/Application Support/Vibe Remote/bin/renamed connector"

    assert remote_access._is_cloudflared_command(command, configured)
    assert not remote_access._is_cloudflared_command(command)


def test_safe_extract_cloudflared_rejects_path_traversal(tmp_path):
    archive_path = tmp_path / "cloudflared.tgz"
    target = tmp_path / "cloudflared"
    data = b"malicious"

    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("../../outside/cloudflared")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    with tarfile.open(archive_path, "r:gz") as archive:
        with pytest.raises(RuntimeError, match="unsafe cloudflared path"):
            remote_access._safe_extract_cloudflared(archive, target)

    assert not target.exists()


def test_install_cloudflared_returns_structured_download_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))

    monkeypatch.setattr(remote_access, "_asset_name", lambda: "cloudflared-linux-amd64")
    monkeypatch.setattr(remote_access.urllib.request, "urlretrieve", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))

    result = remote_access.install_cloudflared()

    assert result["ok"] is False
    assert result["error"] == "cloudflared_install_failed"
    assert "offline" in result["detail"]
