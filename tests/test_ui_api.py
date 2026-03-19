from vibe import api


def test_detect_cli_prefers_claude_local(monkeypatch, tmp_path):
    claude_path = tmp_path / ".claude" / "local" / "claude"
    claude_path.parent.mkdir(parents=True, exist_ok=True)
    claude_path.write_text("#!/bin/sh\n")
    claude_path.chmod(0o755)

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)
    result = api.detect_cli("claude")
    assert result["found"] is True
    assert result["path"] == str(claude_path)


def test_detect_cli_finds_opencode_installed_outside_path(monkeypatch, tmp_path):
    opencode_path = tmp_path / ".opencode" / "bin" / "opencode"
    opencode_path.parent.mkdir(parents=True, exist_ok=True)
    opencode_path.write_text("#!/bin/sh\n")
    opencode_path.chmod(0o755)

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(api.shutil, "which", lambda binary: None)

    result = api.detect_cli("opencode")

    assert result["found"] is True
    assert result["path"] == str(opencode_path)


def test_detect_cli_supports_explicit_path(monkeypatch, tmp_path):
    binary_path = tmp_path / "bin" / "custom-opencode"
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    binary_path.write_text("#!/bin/sh\n")
    binary_path.chmod(0o755)

    monkeypatch.setattr(api.shutil, "which", lambda binary: None)

    result = api.detect_cli(str(binary_path))

    assert result["found"] is True
    assert result["path"] == str(binary_path)


def test_detect_cli_finds_npm_in_nvm(monkeypatch, tmp_path):
    npm_path = tmp_path / ".nvm" / "versions" / "node" / "v22.18.0" / "bin" / "npm"
    npm_path.parent.mkdir(parents=True, exist_ok=True)
    npm_path.write_text("#!/bin/sh\n")
    npm_path.chmod(0o755)

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(api.shutil, "which", lambda binary: None)

    result = api.detect_cli("npm")

    assert result["found"] is True
    assert result["path"] == str(npm_path)


def test_install_agent_returns_resolved_path(monkeypatch):
    class CompletedProcess:
        returncode = 0
        stdout = "installed"
        stderr = ""

    monkeypatch.setattr(
        api.shutil,
        "which",
        lambda binary: f"/usr/bin/{binary}" if binary in {"curl", "bash"} else None,
    )
    monkeypatch.setattr(api.subprocess, "run", lambda *args, **kwargs: CompletedProcess())
    monkeypatch.setattr(api, "resolve_cli_path", lambda binary: "/Users/test/.opencode/bin/opencode")

    result = api.install_agent("opencode")

    assert result["ok"] is True
    assert result["path"] == "/Users/test/.opencode/bin/opencode"


def test_install_codex_uses_resolved_npm(monkeypatch):
    calls = []

    class CompletedProcess:
        returncode = 0
        stdout = "installed"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env", {})))
        return CompletedProcess()

    def fake_resolve(binary):
        if binary == "npm":
            return "/Users/test/.nvm/versions/node/v22.18.0/bin/npm"
        if binary == "codex":
            return "/Users/test/.nvm/versions/node/v22.18.0/bin/codex"
        return None

    monkeypatch.setattr(api.subprocess, "run", fake_run)
    monkeypatch.setattr(api, "resolve_cli_path", fake_resolve)

    result = api.install_agent("codex")

    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0][0] == ["/Users/test/.nvm/versions/node/v22.18.0/bin/npm", "install", "-g", "@openai/codex"]
    assert calls[0][1]["PATH"].split(api.os.pathsep)[0] == "/Users/test/.nvm/versions/node/v22.18.0/bin"
    assert result["path"] == "/Users/test/.nvm/versions/node/v22.18.0/bin/codex"
