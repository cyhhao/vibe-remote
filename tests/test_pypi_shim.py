from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHIM_PROJECT = ROOT / "packaging" / "vibe-remote-shim"
PREPARE_SCRIPT = ROOT / "scripts" / "prepare_legacy_pypi_shim.py"


def test_legacy_pypi_shim_metadata_points_to_avibe_os() -> None:
    metadata = tomllib.loads((SHIM_PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    assert project["name"] == "vibe-remote"
    assert project["version"] == "3.0.0"
    assert project["dependencies"] == ["avibe-os==3.0.0"]
    assert project["scripts"] == {"vibe": "vibe.cli:main"}


def test_legacy_pypi_shim_contains_no_runtime_packages() -> None:
    metadata = tomllib.loads((SHIM_PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = metadata["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert wheel["packages"] == ["vibe_remote_shim"]
    assert not any((SHIM_PROJECT / package).exists() for package in ("vibe", "config", "core", "modules", "storage"))


def test_prepare_legacy_shim_pins_matching_3_0_patch_version(tmp_path: Path) -> None:
    project = tmp_path / "vibe-remote-shim"
    shutil.copytree(SHIM_PROJECT, project)

    result = subprocess.run(
        [sys.executable, str(PREPARE_SCRIPT), "--tag", "v3.0.1", "--project-dir", str(project)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == "3.0.1"
    assert metadata["project"]["dependencies"] == ["avibe-os==3.0.1"]


def test_prepare_legacy_shim_rejects_3_1_and_later(tmp_path: Path) -> None:
    project = tmp_path / "vibe-remote-shim"
    shutil.copytree(SHIM_PROJECT, project)

    result = subprocess.run(
        [sys.executable, str(PREPARE_SCRIPT), "--tag", "v3.1.0", "--project-dir", str(project)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == "3.0.0"
