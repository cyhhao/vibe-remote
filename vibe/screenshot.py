"""Local screenshot capture helpers for the Vibe Remote CLI."""

from __future__ import annotations

import os
import sys
import tempfile
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from shutil import which

from config import paths


class ScreenshotError(RuntimeError):
    """Raised when a local screenshot cannot be captured."""


@dataclass(frozen=True)
class ScreenshotResult:
    path: Path
    backend: str


def default_screenshot_dir() -> Path:
    return paths.get_vibe_remote_dir() / "screenshots"


def default_screenshot_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    directory = default_screenshot_dir()
    return directory / f"screenshot_{timestamp}_{uuid.uuid4().hex[:8]}.png"


def capture_screenshot(output: str | Path | None = None) -> ScreenshotResult:
    """Capture the local desktop to a PNG file."""
    try:
        output_path = _resolve_output_path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path = _temporary_output_path(output_path)
    except OSError as exc:
        raise ScreenshotError(f"failed to prepare screenshot output path: {exc}") from exc

    try:
        if sys.platform == "darwin":
            backend = _capture_macos(capture_path)
        elif sys.platform.startswith("win"):
            backend = _capture_windows(capture_path)
        elif sys.platform.startswith("linux"):
            backend = _capture_linux(capture_path)
        else:
            raise ScreenshotError(f"screenshots are not supported on this platform: {sys.platform}")

        _verify_output(capture_path)
        try:
            capture_path.replace(output_path)
        except OSError as exc:
            raise ScreenshotError(f"failed to finalize screenshot output: {exc}") from exc
    finally:
        try:
            capture_path.unlink(missing_ok=True)
        except OSError:
            pass
    return ScreenshotResult(path=output_path, backend=backend)


def _resolve_output_path(output: str | Path | None) -> Path:
    path = Path(output).expanduser() if output else default_screenshot_path()
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.suffix.lower() != ".png":
        path = path.with_suffix(".png")
    return path.resolve()


def _temporary_output_path(output: Path) -> Path:
    fd, raw_path = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp.png",
        dir=output.parent,
    )
    try:
        os.close(fd)
    except OSError:
        pass
    Path(raw_path).unlink()
    return Path(raw_path)


def _capture_macos(output: Path) -> str:
    if which("screencapture") is None:
        raise ScreenshotError("macOS screencapture command was not found")
    _run_capture_command(["screencapture", "-x", str(output)], "screencapture")
    return "screencapture"


def _capture_windows(output: Path) -> str:
    executable = which("powershell") or which("pwsh")
    if executable is None:
        raise ScreenshotError("PowerShell was not found")

    script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$path = $args[0]
$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bounds.Size)
$bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()
"""
    _run_capture_command(
        [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script, str(output)],
        "PowerShell screenshot capture",
    )
    return "powershell"


def _capture_linux(output: Path) -> str:
    candidates = [
        ("grim", ["grim", str(output)]),
        ("gnome-screenshot", ["gnome-screenshot", "-f", str(output)]),
        ("spectacle", ["spectacle", "-b", "-n", "-o", str(output)]),
        ("scrot", ["scrot", str(output)]),
        ("import", ["import", "-window", "root", str(output)]),
    ]
    attempted: list[str] = []
    for name, command in candidates:
        if which(name) is None:
            continue
        attempted.append(name)
        try:
            _run_capture_command(command, name)
            return name
        except ScreenshotError:
            pass

    if attempted:
        raise ScreenshotError(f"installed screenshot tools failed: {', '.join(attempted)}")
    raise ScreenshotError("no Linux screenshot tool found; install grim, gnome-screenshot, spectacle, scrot, or ImageMagick")


def _run_capture_command(command: list[str], label: str) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise ScreenshotError(f"{label} timed out after {exc.timeout:g} seconds") from exc
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout or "").strip()
    if detail:
        raise ScreenshotError(f"{label} failed: {detail}")
    raise ScreenshotError(f"{label} failed with exit code {result.returncode}")


def _verify_output(output: Path) -> None:
    try:
        if not output.is_file():
            raise ScreenshotError(f"screenshot file was not created: {output}")
        if output.stat().st_size > 0:
            return
        output.unlink(missing_ok=True)
    except ScreenshotError:
        raise
    except OSError as exc:
        raise ScreenshotError(f"failed to inspect screenshot output: {exc}") from exc
    raise ScreenshotError("screenshot file was empty")
