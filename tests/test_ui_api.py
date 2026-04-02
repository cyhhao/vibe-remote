import json

import pytest

from config import paths
from config.discovered_chats import DiscoveredChatsStore
from vibe import api
from vibe.opencode_config import parse_jsonc_object


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

def test_claude_models_merge_builtin_cli_and_settings(monkeypatch, tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "settings.json").write_text(
        json.dumps(
            {
                "model": "opus[1m]",
                "env": {
                    "ANTHROPIC_MODEL": "claude-sonnet-4-6",
                    "ANTHROPIC_SMALL_FAST_MODEL": "claude-haiku-4-5-20251001",
                },
            }
        ),
        encoding="utf-8",
    )
    cli_bundle = tmp_path / "claude-cli.js"
    cli_bundle.write_text(
        '\n'.join(
            [
                'const OPUS_ID = "claude-opus-4-6";',
                'const SONNET_ID = "claude-sonnet-4-6";',
                'const HAIKU_ID = "claude-haiku-4-5";',
                'const PREV_SONNET_ID = "claude-sonnet-4-5";',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(api, "resolve_cli_path", lambda binary: str(cli_bundle) if binary == "claude" else None)

    result = api.claude_models()

    assert result["ok"] is True
    assert result["models"][:4] == [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-opus-4-5",
    ]
    assert "opus" in result["models"]
    assert "sonnet" in result["models"]
    assert "haiku" in result["models"]
    assert "opus[1m]" in result["models"]
    assert "claude-haiku-4-5-20251001" in result["models"]
    assert result["models"].count("claude-sonnet-4-6") == 1
    assert [item["value"] for item in result["reasoning_options"]["opus"]] == [
        "__default__",
        "low",
        "medium",
        "high",
        "max",
    ]


def test_codex_models_prefers_cli_cache_and_filters_hidden_models(monkeypatch, tmp_path):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    (codex_dir / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "gpt-5.1", "visibility": "hide", "priority": 9},
                    {"slug": "gpt-5.3-codex-spark", "visibility": "list", "priority": 6},
                    {"slug": "gpt-5.4-mini", "visibility": "list", "priority": 3},
                    {"slug": "gpt-5.4", "visibility": "list", "priority": 1},
                    {"slug": "gpt-5.1-codex-mini", "visibility": "list", "priority": 19},
                ]
            }
        ),
        encoding="utf-8",
    )
    (codex_dir / "config.toml").write_text(
        '\n'.join(
            [
                'model = "gpt-5.1"',
                "[notice.model_migrations]",
                '"gpt-5.2" = "gpt-5.4"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)

    result = api.codex_models()

    assert result["ok"] is True
    assert result["models"][:3] == [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
    ]
    assert "gpt-5.3-codex-spark" in result["models"]
    assert "gpt-5.1-codex-mini" in result["models"]
    assert "gpt-5.1" in result["models"]
    assert "gpt-5.2" in result["models"]
    assert result["models"].count("gpt-5.4") == 1


def test_codex_models_falls_back_when_cli_cache_missing(monkeypatch, tmp_path):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    (codex_dir / "config.toml").write_text(
        '\n'.join(
            [
                'model = "custom-codex-model"',
                "[notice.model_migrations]",
                '"legacy-codex" = "gpt-5.4"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)

    result = api.codex_models()

    assert result["ok"] is True
    assert result["models"][:3] == ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"]
    assert "custom-codex-model" in result["models"]
    assert "legacy-codex" in result["models"]
    assert "gpt-5.1-codex-max" in result["models"]
    assert "gpt-5.1-codex-mini" in result["models"]


def test_codex_agents_merges_global_and_project(monkeypatch, tmp_path):
    global_agent_dir = tmp_path / ".codex" / "agents"
    global_agent_dir.mkdir(parents=True)
    (global_agent_dir / "reviewer.toml").write_text(
        '\n'.join(
            [
                'name = "reviewer"',
                'description = "Global reviewer"',
                'developer_instructions = "Review carefully."',
                'model = "gpt-5.4-mini"',
                'model_reasoning_effort = "medium"',
            ]
        ),
        encoding="utf-8",
    )

    project_root = tmp_path / "repo"
    project_agent_dir = project_root / ".codex" / "agents"
    project_agent_dir.mkdir(parents=True)
    (project_agent_dir / "reviewer.toml").write_text(
        '\n'.join(
            [
                'name = "reviewer"',
                'description = "Project reviewer"',
                'developer_instructions = "Focus on local changes."',
                'model = "gpt-5.4"',
                'model_reasoning_effort = "high"',
            ]
        ),
        encoding="utf-8",
    )
    (project_agent_dir / "triage.toml").write_text(
        '\n'.join(
            [
                'name = "triage"',
                'description = "Project triage"',
                'developer_instructions = "Classify issues first."',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)

    result = api.codex_agents(str(project_root))

    assert result["ok"] is True
    assert [agent["id"] for agent in result["agents"]] == ["reviewer", "triage"]
    assert result["agents"][0]["source"] == "project"
    assert result["agents"][0]["description"] == "Project reviewer"
    assert result["agents"][0]["path"] == str(project_agent_dir / "reviewer.toml")
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
    updated = parse_jsonc_object(config_path.read_text(encoding="utf-8"))

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
    updated_text = config_path.read_text(encoding="utf-8")
    updated = parse_jsonc_object(updated_text)

    assert result["ok"] is True
    assert result["config_path"] == str(config_path)
    assert "// Global defaults should be preserved." in updated_text
    assert '"permission": "allow",' in updated_text
    assert '"model": "anthropic/claude-sonnet-4-5",' in updated_text
    assert updated == {
        "model": "openai/gpt-5",
        "agent": {"build": {"model": "anthropic/claude-sonnet-4-5"}},
        "permission": "allow",
    }


def test_setup_opencode_permission_preserves_existing_permission_node(monkeypatch, tmp_path):
    config_path = tmp_path / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(
        {
            "model": "openai/gpt-5",
            "permission": "allow",
        },
        indent=2,
    ) + "\n"
    config_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)

    result = api.setup_opencode_permission()

    assert result == {
        "ok": True,
        "message": "Permission already set",
        "config_path": str(config_path),
    }
    assert config_path.read_text(encoding="utf-8") == original


def test_setup_opencode_permission_does_not_overwrite_invalid_existing_config(monkeypatch, tmp_path):
    config_path = tmp_path / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    original = '{\n  "model": "openai/gpt-5",\n'
    config_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)

    result = api.setup_opencode_permission()

    assert result["ok"] is False
    assert result["config_path"] == str(config_path)
    assert "could not be parsed" in result["message"]
    assert "File left unchanged." in result["message"]
    assert config_path.read_text(encoding="utf-8") == original


def test_setup_opencode_permission_preserves_comments_when_updating_existing_permission(monkeypatch, tmp_path):
    config_path = tmp_path / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """{
  "model": "openai/gpt-5",
  "permission": /* keep this block comment */ "prompt", // keep this inline comment
}
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)

    result = api.setup_opencode_permission()
    updated_text = config_path.read_text(encoding="utf-8")

    assert result["ok"] is True
    assert result["config_path"] == str(config_path)
    assert '/* keep this block comment */ "allow", // keep this inline comment' in updated_text
    assert parse_jsonc_object(updated_text) == {
        "model": "openai/gpt-5",
        "permission": "allow",
    }


def test_setup_opencode_permission_handles_multiline_object_with_inline_closing_brace(monkeypatch, tmp_path):
    config_path = tmp_path / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """{
  "model": "openai/gpt-5"}""",
        encoding="utf-8",
    )

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)

    result = api.setup_opencode_permission()
    updated_text = config_path.read_text(encoding="utf-8")

    assert result["ok"] is True
    assert result["config_path"] == str(config_path)
    assert updated_text == """{
  "model": "openai/gpt-5",
  "permission": "allow"
}"""
    assert parse_jsonc_object(updated_text) == {
        "model": "openai/gpt-5",
        "permission": "allow",
    }


def test_setup_opencode_permission_updates_last_duplicate_permission_entry(monkeypatch, tmp_path):
    config_path = tmp_path / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """{
  "permission": "prompt",
  "model": "openai/gpt-5",
  "permission": "deny"
}
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)

    result = api.setup_opencode_permission()
    updated_text = config_path.read_text(encoding="utf-8")

    assert result["ok"] is True
    assert result["config_path"] == str(config_path)
    assert updated_text == """{
  "permission": "prompt",
  "model": "openai/gpt-5",
  "permission": "allow"
}
"""
    assert parse_jsonc_object(updated_text) == {
        "permission": "allow",
        "model": "openai/gpt-5",
    }


def test_setup_opencode_permission_preserves_leading_bom_when_inserting_multiline_property(
    monkeypatch, tmp_path
):
    config_path = tmp_path / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\ufeff{\n  \"model\": \"openai/gpt-5\"\n}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)

    result = api.setup_opencode_permission()
    updated_text = config_path.read_text(encoding="utf-8")

    assert result["ok"] is True
    assert result["config_path"] == str(config_path)
    assert updated_text.startswith("\ufeff{\n")
    assert updated_text.count("\ufeff") == 1
    assert parse_jsonc_object(updated_text) == {
        "model": "openai/gpt-5",
        "permission": "allow",
    }


def test_setup_opencode_permission_skips_comment_only_file_and_uses_next_valid_path(monkeypatch, tmp_path):
    xdg_path = tmp_path / ".config" / "opencode" / "opencode.json"
    legacy_path = tmp_path / ".opencode" / "opencode.json"
    xdg_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    xdg_path.write_text("// placeholder only\n", encoding="utf-8")
    legacy_path.write_text(
        """{
  "model": "openai/gpt-5",
}
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)

    result = api.setup_opencode_permission()
    updated = parse_jsonc_object(legacy_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["config_path"] == str(legacy_path)
    assert xdg_path.read_text(encoding="utf-8") == "// placeholder only\n"
    assert updated == {
        "model": "openai/gpt-5",
        "permission": "allow",
    }


def test_setup_opencode_permission_returns_error_when_existing_config_update_fails(monkeypatch, tmp_path):
    config_path = tmp_path / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('{"model": "openai/gpt-5"}\n', encoding="utf-8")

    original_write_text = api.Path.write_text

    def failing_write_text(self, data, encoding=None, errors=None, newline=None):
        if self == config_path:
            raise OSError("read-only file system")
        return original_write_text(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(api.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(api.Path, "write_text", failing_write_text)

    result = api.setup_opencode_permission()

    assert result == {
        "ok": False,
        "message": "read-only file system",
        "config_path": str(config_path),
    }
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"model": "openai/gpt-5"}


def test_parse_jsonc_object_preserves_comment_markers_inside_strings():
    parsed = parse_jsonc_object(
        """{
  "line": "https://example.com // keep",
  "block": "value /* keep */ text"
}"""
    )

    assert parsed == {
        "line": "https://example.com // keep",
        "block": "value /* keep */ text",
    }


def test_parse_jsonc_object_accepts_inline_block_comments_before_values():
    parsed = parse_jsonc_object(
        """{
  "model": /* keep this comment */ "openai/gpt-5",
  "agent": {
    "build": /* another comment */ {
      "reasoningEffort": "high",
    },
  },
}"""
    )

    assert parsed == {
        "model": "openai/gpt-5",
        "agent": {
            "build": {
                "reasoningEffort": "high",
            }
        },
    }


def test_parse_jsonc_object_rejects_invalid_jsonc():
    with pytest.raises(json.JSONDecodeError):
        parse_jsonc_object(
            """{
  "model": "openai/gpt-5",
  "agent": {
    "build":
  }
}"""
        )


def test_telegram_auth_test_returns_response(monkeypatch):
    async def fake_get_me(bot_token: str):
        assert bot_token == "123456:test-token"
        return {"id": 1, "username": "vibe_remote_bot"}

    monkeypatch.setattr(api, "_telegram_get_me", fake_get_me)

    result = api.telegram_auth_test("123456:test-token")

    assert result["ok"] is True
    assert result["response"]["username"] == "vibe_remote_bot"


def test_telegram_list_chats_returns_discovered_groups(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_vibe_remote_dir", lambda: tmp_path / ".vibe_remote")
    DiscoveredChatsStore.reset_instance()
    store = DiscoveredChatsStore.get_instance()
    store.remember_chat(platform="telegram", chat_id="-1001", name="Core Group", chat_type="supergroup")
    store.remember_chat(platform="telegram", chat_id="42", name="Alex", chat_type="private", is_private=True)

    result = api.telegram_list_chats()

    assert result["ok"] is True
    assert [chat["id"] for chat in result["channels"]] == ["-1001"]
    assert result["summary"]["visible_count"] == 1
    assert result["summary"]["hidden_private_count"] == 1
    DiscoveredChatsStore.reset_instance()
