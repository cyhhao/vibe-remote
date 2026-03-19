from __future__ import annotations

import sys
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_utils_module():
    module_path = Path(__file__).resolve().parents[1] / "modules" / "agents" / "opencode" / "utils.py"
    spec = importlib.util.spec_from_file_location("opencode_utils_for_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_utils = _load_utils_module()
build_claude_reasoning_options = _utils.build_claude_reasoning_options
normalize_claude_reasoning_effort = _utils.normalize_claude_reasoning_effort


def test_claude_reasoning_options_default_to_low_medium_high() -> None:
    options = build_claude_reasoning_options(None)

    assert [item["value"] for item in options] == ["__default__", "low", "medium", "high"]


def test_claude_reasoning_options_add_max_for_opus_46() -> None:
    options = build_claude_reasoning_options("claude-opus-4-6")

    assert [item["value"] for item in options] == ["__default__", "low", "medium", "high", "max"]


def test_claude_reasoning_options_do_not_add_max_for_sonnet_46() -> None:
    options = build_claude_reasoning_options("claude-sonnet-4-6")

    assert [item["value"] for item in options] == ["__default__", "low", "medium", "high"]


def test_normalize_claude_reasoning_effort_drops_invalid_max() -> None:
    assert normalize_claude_reasoning_effort("claude-sonnet-4-6", "max") is None
    assert normalize_claude_reasoning_effort("claude-opus-4-6", "max") == "max"
