"""Hermetic tests for the askill local-dependency helpers in vibe/api.py.

The subprocess / path-resolution boundary is monkeypatched, so these run
without askill, npm, or the network — they pin the install command
construction, the idempotency of ``ensure_askill_installed``, and the status
shape.
"""

from __future__ import annotations

from vibe import api


def test_install_askill_uses_official_curl_installer(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(api, "resolve_cli_path", lambda b: f"/usr/bin/{b}" if b in {"curl", "bash"} else None)

    def fake_run(name, cmd, _trunc, *, mode="install", env=None):
        captured.update(name=name, cmd=cmd, mode=mode)
        return {"ok": True, "path": "/usr/local/bin/askill", "output": ""}

    monkeypatch.setattr(api, "_run_install_command", fake_run)
    out = api.install_askill()
    assert out["ok"]
    assert captured["name"] == "askill"
    assert captured["cmd"][:2] == ["bash", "-c"]
    assert "curl -fsSL https://askill.sh | sh" in captured["cmd"][2]


def test_install_askill_npm_fallback(monkeypatch):
    captured: dict = {}
    # No curl/bash -> npm fallback.
    monkeypatch.setattr(api, "resolve_cli_path", lambda b: "/n/npm" if b == "npm" else None)
    monkeypatch.setattr(api, "_command_env_for", lambda p: {"PATH": "/n"})

    def fake_run(name, cmd, _trunc, *, mode="install", env=None):
        captured["cmd"] = cmd
        return {"ok": True}

    monkeypatch.setattr(api, "_run_install_command", fake_run)
    api.install_askill()
    assert captured["cmd"] == ["/n/npm", "install", "-g", "askill-cli"]


def test_ensure_askill_idempotent_when_present(monkeypatch):
    monkeypatch.setattr(api, "resolve_cli_path", lambda b: "/usr/local/bin/askill")
    flag = {"installed": False}
    monkeypatch.setattr(api, "install_askill", lambda: flag.__setitem__("installed", True) or {"ok": True})
    out = api.ensure_askill_installed()
    assert out == {"ok": True, "installed": True, "changed": False, "path": "/usr/local/bin/askill"}
    assert flag["installed"] is False  # never installed when already present


def test_ensure_askill_installs_when_missing(monkeypatch):
    monkeypatch.setattr(api, "resolve_cli_path", lambda b: None)
    monkeypatch.setattr(api, "install_askill", lambda: {"ok": True, "path": "/x/askill"})
    out = api.ensure_askill_installed()
    assert out["ok"] and out["installed"] and out["changed"]


def test_ensure_askill_force_reinstalls_even_when_present(monkeypatch):
    monkeypatch.setattr(api, "resolve_cli_path", lambda b: "/usr/local/bin/askill")
    flag = {"installed": False}
    monkeypatch.setattr(api, "install_askill", lambda: flag.__setitem__("installed", True) or {"ok": True})
    api.ensure_askill_installed(force=True)
    assert flag["installed"] is True


def test_askill_status_missing(monkeypatch):
    monkeypatch.setattr(api, "resolve_cli_path", lambda b: None)
    s = api.askill_status()
    assert s["installed"] is False and s["status"] == "missing" and s["version"] is None


def test_askill_status_present_parses_version(monkeypatch):
    monkeypatch.setattr(api, "resolve_cli_path", lambda b: "/x/askill")
    monkeypatch.setattr(api, "_command_env_for", lambda p: {})
    monkeypatch.setattr(api, "isolated_subprocess_kwargs", lambda: {})

    class _R:
        returncode = 0
        stdout = "askill 0.1.13\n"
        stderr = ""

    monkeypatch.setattr(api.subprocess, "run", lambda *a, **k: _R())
    s = api.askill_status()
    assert s["installed"] and s["version"] == "0.1.13" and s["status"] == "ready"
