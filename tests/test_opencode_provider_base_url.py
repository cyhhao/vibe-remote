"""Unit tests for the OpenCode provider ``baseURL`` config helpers.

The Settings → Backends → OpenCode page exposes a Base URL input per
provider; without these helpers the input is a no-op because OpenCode's
own ``PUT /auth/{provider_id}`` endpoint has no field for it. These
tests pin the round-trip (upsert → read → remove) and the prune
behaviour so a future refactor cannot silently regress the UI back into
"save success, value lost on reload" — the bug Codex flagged in
``PR #282`` round 3.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe.opencode_config import (
    get_opencode_config_paths,
    read_opencode_provider_base_url,
    remove_opencode_provider_base_url,
    upsert_opencode_provider_base_url,
    upsert_opencode_provider_api_key,
)


def _read_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_upsert_base_url_writes_canonical_path(tmp_path: Path) -> None:
    target = upsert_opencode_provider_base_url(
        "openai",
        "https://ai-relay.example/v1",
        home=tmp_path,
    )
    assert target == get_opencode_config_paths(tmp_path)[0]
    config = _read_config(target)
    assert config["provider"]["openai"]["options"]["baseURL"] == "https://ai-relay.example/v1"
    assert config["$schema"] == "https://opencode.ai/config.json"


def test_upsert_base_url_coexists_with_api_key(tmp_path: Path) -> None:
    upsert_opencode_provider_api_key("openai", "sk-xxx", home=tmp_path)
    upsert_opencode_provider_base_url("openai", "https://gw.example/v1", home=tmp_path)
    config = _read_config(get_opencode_config_paths(tmp_path)[0])
    options = config["provider"]["openai"]["options"]
    assert options["apiKey"] == "sk-xxx"
    assert options["baseURL"] == "https://gw.example/v1"


def test_upsert_base_url_overrides_previous_value(tmp_path: Path) -> None:
    upsert_opencode_provider_base_url("openai", "https://old.example/v1", home=tmp_path)
    upsert_opencode_provider_base_url("openai", "https://new.example/v1", home=tmp_path)
    config = _read_config(get_opencode_config_paths(tmp_path)[0])
    assert config["provider"]["openai"]["options"]["baseURL"] == "https://new.example/v1"


def test_read_base_url_returns_none_when_unset(tmp_path: Path) -> None:
    # No config file yet → nothing to read.
    assert read_opencode_provider_base_url("openai", home=tmp_path) is None

    # Config exists but provider does not.
    upsert_opencode_provider_api_key("anthropic", "sk-anth", home=tmp_path)
    assert read_opencode_provider_base_url("openai", home=tmp_path) is None


def test_read_base_url_returns_persisted_value(tmp_path: Path) -> None:
    upsert_opencode_provider_base_url(
        "openai",
        "https://ai-relay.example/v1",
        home=tmp_path,
    )
    assert (
        read_opencode_provider_base_url("openai", home=tmp_path)
        == "https://ai-relay.example/v1"
    )


def test_remove_base_url_prunes_empty_options(tmp_path: Path) -> None:
    upsert_opencode_provider_base_url("openai", "https://gw.example/v1", home=tmp_path)
    remove_opencode_provider_base_url("openai", home=tmp_path)

    config = _read_config(get_opencode_config_paths(tmp_path)[0])
    # With both apiKey and baseURL gone, the provider block prunes
    # itself; with no provider blocks left, the ``provider`` key is
    # dropped entirely. The ``$schema`` planted by upsert stays.
    assert "provider" not in config
    assert config.get("$schema") == "https://opencode.ai/config.json"


def test_remove_base_url_preserves_api_key(tmp_path: Path) -> None:
    upsert_opencode_provider_api_key("openai", "sk-xxx", home=tmp_path)
    upsert_opencode_provider_base_url("openai", "https://gw.example/v1", home=tmp_path)
    remove_opencode_provider_base_url("openai", home=tmp_path)

    config = _read_config(get_opencode_config_paths(tmp_path)[0])
    options = config["provider"]["openai"]["options"]
    assert options == {"apiKey": "sk-xxx"}


def test_remove_base_url_is_idempotent(tmp_path: Path) -> None:
    upsert_opencode_provider_base_url("openai", "https://gw.example/v1", home=tmp_path)
    remove_opencode_provider_base_url("openai", home=tmp_path)
    # Second call must be a no-op rather than raise.
    remove_opencode_provider_base_url("openai", home=tmp_path)
    assert read_opencode_provider_base_url("openai", home=tmp_path) is None


@pytest.mark.parametrize(
    "base_url_value",
    ["   ", ""],
)
def test_read_base_url_ignores_blank_values(tmp_path: Path, base_url_value: str) -> None:
    # Write a value that contains no useful content; the helper should
    # treat it as "not configured" so the UI does not show whitespace as
    # the persisted override.
    upsert_opencode_provider_base_url("openai", "https://x.example", home=tmp_path)
    target = get_opencode_config_paths(tmp_path)[0]
    config = _read_config(target)
    config["provider"]["openai"]["options"]["baseURL"] = base_url_value
    target.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    assert read_opencode_provider_base_url("openai", home=tmp_path) is None


def test_remove_then_reupsert_round_trip(tmp_path: Path) -> None:
    # Once ``remove`` has pruned the entire ``provider`` block (the only
    # provider had its last option removed), a subsequent ``upsert`` has
    # to scaffold the JSON structure back from scratch — exercise that
    # path so a future refactor doesn't regress to a KeyError.
    upsert_opencode_provider_base_url("openai", "https://old.example", home=tmp_path)
    remove_opencode_provider_base_url("openai", home=tmp_path)
    upsert_opencode_provider_base_url("openai", "https://new.example", home=tmp_path)
    assert (
        read_opencode_provider_base_url("openai", home=tmp_path)
        == "https://new.example"
    )


def test_remove_one_provider_keeps_other_untouched(tmp_path: Path) -> None:
    upsert_opencode_provider_base_url("openai", "https://a.example", home=tmp_path)
    upsert_opencode_provider_base_url("anthropic", "https://b.example", home=tmp_path)
    upsert_opencode_provider_api_key("anthropic", "sk-anth", home=tmp_path)

    remove_opencode_provider_base_url("openai", home=tmp_path)

    config = _read_config(get_opencode_config_paths(tmp_path)[0])
    assert "openai" not in config.get("provider", {})
    # Anthropic still has both apiKey and baseURL untouched.
    assert config["provider"]["anthropic"]["options"] == {
        "apiKey": "sk-anth",
        "baseURL": "https://b.example",
    }
