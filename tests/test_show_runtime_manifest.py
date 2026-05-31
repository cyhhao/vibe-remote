import json
from pathlib import Path

import pytest

from scripts.generate_show_runtime_manifest import build_manifest


PLATFORMS = (
    "darwin-arm64",
    "darwin-x64",
    "linux-arm64",
    "linux-x64",
    "win32-arm64",
    "win32-x64",
)


def test_generate_show_runtime_manifest_records_all_platform_archives(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    for platform in PLATFORMS:
        (archive_dir / f"vibe-show-runtime-node-{platform}.tgz").write_bytes(f"runtime-{platform}".encode())

    output = tmp_path / "manifest.json"
    manifest = build_manifest(
        archive_dir=archive_dir,
        tag="gh-v2.4.0rc1",
        repo="cyhhao/vibe-remote",
        runtime_ref="runtime-sha",
        output=output,
    )

    assert set(manifest["archives"]) == set(PLATFORMS)
    assert output.exists()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["runtime_version"] == "runtime-sha"
    assert written["archives"]["linux-x64"]["url"] == (
        "https://github.com/cyhhao/vibe-remote/releases/download/gh-v2.4.0rc1/"
        "vibe-show-runtime-node-linux-x64.tgz"
    )
    assert written["archives"]["linux-x64"]["size"] == len(b"runtime-linux-x64")


def test_generate_show_runtime_manifest_fails_when_platform_archive_missing(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    for platform in PLATFORMS[:-1]:
        (archive_dir / f"vibe-show-runtime-node-{platform}.tgz").write_bytes(b"runtime")

    with pytest.raises(SystemExit, match="Missing Show Runtime archives"):
        build_manifest(
            archive_dir=archive_dir,
            tag="v2.4.0",
            repo="cyhhao/vibe-remote",
            runtime_ref="runtime-sha",
            output=tmp_path / "manifest.json",
        )
