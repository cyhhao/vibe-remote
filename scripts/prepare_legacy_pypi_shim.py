#!/usr/bin/env python3
"""Prepare the legacy ``vibe-remote`` PyPI shim for a release tag."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


SUPPORTED_TAG_RE = re.compile(r"^v(?P<version>3\.0\.\d+)$")


def render_pyproject(version: str) -> str:
    return f"""[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "vibe-remote"
version = "{version}"
description = "Legacy PyPI shim for avibe-os"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
authors = [
    {{ name = "cyhhao", email = "cyhhao@users.noreply.github.com" }}
]
dependencies = [
    "avibe-os=={version}",
]

[project.urls]
Homepage = "https://github.com/avibe-bot/avibe"
Repository = "https://github.com/avibe-bot/avibe"
Documentation = "https://github.com/avibe-bot/avibe#readme"
Issues = "https://github.com/avibe-bot/avibe/issues"

[project.scripts]
vibe = "vibe.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["vibe_remote_shim"]
"""


def prepare_shim(tag: str, project_dir: Path) -> str:
    match = SUPPORTED_TAG_RE.match(tag)
    if not match:
        raise ValueError(f"unsupported legacy shim tag: {tag}")

    version = match.group("version")
    pyproject_path = project_dir / "pyproject.toml"
    pyproject_path.write_text(render_pyproject(version), encoding="utf-8")
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Release tag, for example v3.0.1")
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path("packaging/vibe-remote-shim"),
        help="Path to the legacy shim project directory",
    )
    args = parser.parse_args()

    try:
        version = prepare_shim(args.tag, args.project_dir)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Prepared vibe-remote legacy shim {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
