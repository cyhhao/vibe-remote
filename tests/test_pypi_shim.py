from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHIM_PROJECT = ROOT / "packaging" / "vibe-remote-shim"


def test_legacy_pypi_shim_metadata_points_to_avibe_os() -> None:
    metadata = tomllib.loads((SHIM_PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    assert project["name"] == "vibe-remote"
    assert project["version"] == "3.0.0"
    assert project["dependencies"] == ["avibe-os>=3.0.0"]
    assert project["scripts"] == {"vibe": "vibe.cli:main"}


def test_legacy_pypi_shim_contains_no_runtime_packages() -> None:
    metadata = tomllib.loads((SHIM_PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = metadata["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert wheel["packages"] == ["vibe_remote_shim"]
    assert not any((SHIM_PROJECT / package).exists() for package in ("vibe", "config", "core", "modules", "storage"))
