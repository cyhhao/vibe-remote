from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "install.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _write_fake_uv(path: Path, uv_log: Path) -> None:
    _write_executable(
        path,
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        printf '%s' "${{UV_TOOL_BIN_DIR:-}}" > "{uv_log}"

        if [ "$1" != "tool" ] || [ "$2" != "install" ]; then
            exit 1
        fi

        bin_dir="${{UV_TOOL_BIN_DIR:-$HOME/.local/bin}}"
        mkdir -p "$bin_dir"
        cat > "$bin_dir/vibe" <<'EOF'
        #!/usr/bin/env bash
        set -euo pipefail
        if [ "${{1:-}}" = "--help" ]; then
            echo "usage: vibe"
        elif [ "${{1:-}}" = "version" ]; then
            echo "vibe-remote 9.9.9"
        else
            echo "started"
        fi
        EOF
        chmod +x "$bin_dir/vibe"
        """,
    )


def test_install_script_keeps_vibe_available_on_current_path(tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    path_dir = tmp_path / "path-bin"
    path_dir.mkdir()
    uv_log = tmp_path / "uv-tool-bin-dir.txt"

    _write_fake_uv(path_dir / "uv", uv_log)

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PATH"] = os.pathsep.join([str(path_dir), "/usr/bin", "/bin"])

    result = subprocess.run(
        ["bash", "-lc", f'bash "{INSTALL_SCRIPT}" && vibe version'],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "vibe-remote 9.9.9" in result.stdout
    assert uv_log.read_text(encoding="utf-8")


def test_install_script_prefers_new_bin_over_stale_local_bin(tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    local_bin = home_dir / ".local" / "bin"
    local_bin.mkdir(parents=True)
    path_dir = tmp_path / "path-bin"
    path_dir.mkdir()
    uv_log = tmp_path / "uv-tool-bin-dir.txt"

    _write_fake_uv(path_dir / "uv", uv_log)
    _write_executable(
        local_bin / "vibe",
        """\
        #!/usr/bin/env bash
        echo "vibe-remote 0.1.0"
        """,
    )

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PATH"] = os.pathsep.join([str(path_dir), "/usr/bin", "/bin"])

    result = subprocess.run(
        ["bash", "-lc", f'bash "{INSTALL_SCRIPT}" && vibe version'],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "vibe-remote 9.9.9" in result.stdout
    assert "vibe-remote 0.1.0" not in result.stdout


def test_install_script_skips_relative_path_entries_for_tool_bin(tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    relative_bin = tmp_path / "bin"
    relative_bin.mkdir()
    path_dir = tmp_path / "path-bin"
    path_dir.mkdir()
    uv_log = tmp_path / "uv-tool-bin-dir.txt"

    _write_fake_uv(path_dir / "uv", uv_log)

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PATH"] = os.pathsep.join(["bin", str(path_dir), "/usr/bin", "/bin"])

    result = subprocess.run(
        ["bash", "-lc", f'bash "{INSTALL_SCRIPT}" && vibe version'],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    selected_dir = Path(uv_log.read_text(encoding="utf-8"))
    assert selected_dir.is_absolute()
    assert not (relative_bin / "vibe").exists()
