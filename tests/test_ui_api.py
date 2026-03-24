import json

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


def test_detect_cli_finds_codex_in_npm_global_prefix(monkeypatch, tmp_path):
    npm_path = tmp_path / "tools" / "npm"
    npm_path.parent.mkdir(parents=True, exist_ok=True)
    npm_path.write_text("#!/bin/sh\n")
    npm_path.chmod(0o755)

    codex_path = tmp_path / ".npm-global" / "bin" / "codex"
    codex_path.parent.mkdir(parents=True, exist_ok=True)
    codex_path.write_text("#!/bin/sh\n")
    codex_path.chmod(0o755)

    class CompletedProcess:
        returncode = 0
        stdout = f"{tmp_path / '.npm-global'}\n"
        stderr = ""

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(api.shutil, "which", lambda binary: str(npm_path) if binary == "npm" else None)
    monkeypatch.setattr(api.subprocess, "run", lambda *args, **kwargs: CompletedProcess())

    result = api.detect_cli("codex")

    assert result["found"] is True
    assert result["path"] == str(codex_path)


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


def test_install_codex_detects_binary_via_npm_prefix(monkeypatch, tmp_path):
    npm_path = tmp_path / "node" / "bin" / "npm"
    npm_path.parent.mkdir(parents=True, exist_ok=True)
    npm_path.write_text("#!/bin/sh\n")
    npm_path.chmod(0o755)

    prefix_path = tmp_path / ".npm-global"
    codex_path = prefix_path / "bin" / "codex"
    codex_path.parent.mkdir(parents=True, exist_ok=True)
    codex_path.write_text("#!/bin/sh\n")
    codex_path.chmod(0o755)

    calls = []

    class CompletedProcess:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env", {})))
        if cmd == [str(npm_path), "install", "-g", "@openai/codex"]:
            return CompletedProcess(stdout="installed")
        if cmd == [str(npm_path), "config", "get", "prefix"]:
            return CompletedProcess(stdout=f"{prefix_path}\n")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(api.shutil, "which", lambda binary: str(npm_path) if binary == "npm" else None)
    monkeypatch.setattr(api.subprocess, "run", fake_run)

    result = api.install_agent("codex")

    assert result["ok"] is True
    assert result["path"] == str(codex_path)
    assert calls[0][0] == [str(npm_path), "install", "-g", "@openai/codex"]
    assert calls[0][1]["PATH"].split(api.os.pathsep)[0] == str(npm_path.parent)


def test_setup_opencode_permission_preserves_existing_json_fields(monkeypatch, tmp_path):
    config_path = tmp_path / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "model": "openai/gpt-5",
                "agent": {"build": {"model": "anthropic/claude-sonnet-4-5"}},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)

    result = api.setup_opencode_permission()
    updated = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["config_path"] == str(config_path)
    assert updated == {
        "model": "openai/gpt-5",
        "agent": {"build": {"model": "anthropic/claude-sonnet-4-5"}},
        "permission": "allow",
    }


def test_setup_opencode_permission_accepts_jsonc_config(monkeypatch, tmp_path):
    config_path = tmp_path / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """{
  // Global defaults should be preserved.
  "model": "openai/gpt-5",
  "agent": {
    "build": {
      "model": "anthropic/claude-sonnet-4-5",
    },
  },
}
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)

    result = api.setup_opencode_permission()
    updated = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["config_path"] == str(config_path)
    assert updated == {
        "model": "openai/gpt-5",
        "agent": {"build": {"model": "anthropic/claude-sonnet-4-5"}},
        "permission": "allow",
    }
