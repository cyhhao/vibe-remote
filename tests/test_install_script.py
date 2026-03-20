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


def _run(command: str, *, cwd: Path, env: dict[str, str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", command],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


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


def _install(env: dict[str, str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return _run(f'bash "{INSTALL_SCRIPT}"', cwd=cwd, env=env)


def _vibe_version(env: dict[str, str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return _run("vibe version", cwd=cwd, env=env)


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

    install_result = _install(env)
    version_result = _vibe_version(env)

    assert install_result.returncode == 0, install_result.stdout + install_result.stderr
    assert "Run 'vibe' to start the setup wizard" in install_result.stdout
    assert "export PATH=" not in install_result.stdout
    assert version_result.returncode == 0, version_result.stdout + version_result.stderr
    assert "vibe-remote 9.9.9" in version_result.stdout
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

    install_result = _install(env)
    version_result = _vibe_version(env)

    assert install_result.returncode == 0, install_result.stdout + install_result.stderr
    assert version_result.returncode == 0, version_result.stdout + version_result.stderr
    assert "vibe-remote 9.9.9" in version_result.stdout
    assert "vibe-remote 0.1.0" not in version_result.stdout


def test_install_script_prefers_original_path_order(tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    first_dir = tmp_path / "first-bin"
    first_dir.mkdir()
    second_dir = tmp_path / "second-bin"
    second_dir.mkdir()
    uv_log = tmp_path / "uv-tool-bin-dir.txt"

    _write_fake_uv(first_dir / "uv", uv_log)

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PATH"] = os.pathsep.join([str(first_dir), str(second_dir), "/usr/bin", "/bin"])

    install_result = _install(env, cwd=tmp_path)

    assert install_result.returncode == 0, install_result.stdout + install_result.stderr
    assert uv_log.read_text(encoding="utf-8") == str(first_dir)


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

    install_result = _install(env, cwd=tmp_path)

    assert install_result.returncode == 0, install_result.stdout + install_result.stderr
    selected_dir = Path(uv_log.read_text(encoding="utf-8"))
    assert selected_dir.is_absolute()
    assert not (relative_bin / "vibe").exists()


def test_install_script_requires_path_export_when_install_dir_not_on_original_path(tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    uv_dir = tmp_path / "uv-bin"
    uv_dir.mkdir()
    uv_log = tmp_path / "uv-tool-bin-dir.txt"

    _write_fake_uv(uv_dir / "uv", uv_log)
    uv_dir.chmod(0o555)

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PATH"] = os.pathsep.join([str(uv_dir), "/usr/bin", "/bin"])

    install_result = _install(env, cwd=tmp_path)
    version_result = _vibe_version(env, cwd=tmp_path)
    uv_dir.chmod(0o755)

    assert install_result.returncode == 0, install_result.stdout + install_result.stderr
    assert 'export PATH="' in install_result.stdout
    assert str(home_dir / ".local" / "bin") in install_result.stdout
    assert version_result.returncode != 0
