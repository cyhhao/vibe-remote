from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibe import api


def _make_paths(tmp_path: Path) -> tuple[str, str]:
    """Create a node shim + native codex binary on disk under tmp_path.

    Returns (node_path, codex_shim_path) so tests can pretend the kernel
    exec'd them as a #!/usr/bin/env node shebang script.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    node_path = bin_dir / "node"
    codex_path = bin_dir / "codex"
    node_path.write_text("#!/bin/sh\n")
    codex_path.write_text("#!/usr/bin/env node\n")
    return str(node_path), str(codex_path)


def test_matches_native_codex_binary(tmp_path: Path) -> None:
    """argv[0] is the codex binary itself (native install)."""
    _, codex_path = _make_paths(tmp_path)
    cmdline = [codex_path, "app-server", "--analytics-default-enabled"]
    assert api._process_matches_codex_binary(cmdline, codex_path) is True


def test_matches_node_shim_codex(tmp_path: Path) -> None:
    """argv[0] is node, argv[1] is the codex shebang script (npm install)."""
    node_path, codex_path = _make_paths(tmp_path)
    cmdline = [node_path, codex_path, "app-server"]
    assert api._process_matches_codex_binary(cmdline, codex_path) is True


def test_matches_node_shim_with_extra_node_flags(tmp_path: Path) -> None:
    """Some node shims insert flags between node and the script path.

    psutil reports cmdline including the interpreter and its flags, so we
    should still tolerate one such flag without sweeping in unrelated tools.
    """
    node_path, codex_path = _make_paths(tmp_path)
    cmdline = [node_path, codex_path, "app-server", "--something"]
    assert api._process_matches_codex_binary(cmdline, codex_path) is True


def test_rejects_unrelated_node_process(tmp_path: Path) -> None:
    """A node process that happens to mention codex deeper in args is not ours."""
    node_path, _ = _make_paths(tmp_path)
    other = tmp_path / "bin" / "other-tool"
    other.write_text("#!/usr/bin/env node\n")
    # The codex/app-server tokens sit far down the cmdline; should be rejected.
    cmdline = [node_path, str(other), "--config=codex.json", "--mode=app-server"]
    codex_path = str(tmp_path / "bin" / "codex")
    assert api._process_matches_codex_binary(cmdline, codex_path) is False


def test_rejects_different_codex_install(tmp_path: Path) -> None:
    """Another codex install elsewhere on the system is not ours to kill."""
    _, codex_path = _make_paths(tmp_path)
    elsewhere = tmp_path / "other" / "codex"
    elsewhere.parent.mkdir(parents=True, exist_ok=True)
    elsewhere.write_text("#!/usr/bin/env node\n")
    cmdline = [str(elsewhere), "app-server"]
    assert api._process_matches_codex_binary(cmdline, codex_path) is False


def test_basename_fallback_when_no_resolved_binary(tmp_path: Path) -> None:
    """When the configured CLI isn't on PATH, basename starting with codex matches."""
    _, codex_path = _make_paths(tmp_path)
    cmdline = [codex_path, "app-server"]
    assert api._process_matches_codex_binary(cmdline, None) is True


def test_basename_fallback_via_node_shim(tmp_path: Path) -> None:
    """Basename fallback also works for the node-shim case."""
    node_path, codex_path = _make_paths(tmp_path)
    cmdline = [node_path, codex_path, "app-server"]
    assert api._process_matches_codex_binary(cmdline, None) is True


def test_rejects_empty_cmdline() -> None:
    assert api._process_matches_codex_binary([], "/usr/local/bin/codex") is False


def test_requires_app_server_token(tmp_path: Path) -> None:
    """A codex invocation without ``app-server`` (e.g. ``codex login``) is not the daemon."""
    _, codex_path = _make_paths(tmp_path)
    cmdline = [codex_path, "login", "--browser"]
    assert api._process_matches_codex_binary(cmdline, codex_path) is False
