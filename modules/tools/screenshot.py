import os
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = os.path.join(os.path.expanduser("~"), ".vibe_remote", "screenshots")


class ScreenshotError(Exception):
    """Raised when a screenshot capture fails with a specific reason."""


def ensure_screenshot_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _save_cropped_image(full_img, left: int, top: int, right: int, bottom: int, filepath: str) -> bool:
    """Crop a region from a full-screen mss image and save as PNG.

    Returns True on success, False on failure (e.g. empty crop, off-screen window).
    """
    import numpy as np

    c_left = max(0, min(left, full_img.shape[1]))
    c_top = max(0, min(top, full_img.shape[0]))
    c_right = max(0, min(right, full_img.shape[1]))
    c_bottom = max(0, min(bottom, full_img.shape[0]))

    crop_w = c_right - c_left
    crop_h = c_bottom - c_top
    if crop_w <= 0 or crop_h <= 0:
        logger.error(f"Empty crop region: {crop_w}x{crop_h} (window may be off-screen or minimized)")
        return False

    cropped = full_img[c_top:c_bottom, c_left:c_right]
    # mss returns BGRA; reverse BGR -> RGB for correct colors
    rgb_data = cropped[:, :, :3][:, :, ::-1]

    try:
        from PIL import Image as PILImage
        img = PILImage.fromarray(rgb_data)
        img.save(filepath, "PNG")
        return True
    except ImportError:
        mss.tools.to_png(rgb_data.tobytes(), (crop_w, crop_h), output=filepath)
        # Verify the file was created and is not a degenerate PNG
        if os.path.getsize(filepath) < 100:
            logger.error(f"Generated PNG is too small ({os.path.getsize(filepath)} bytes), likely invalid")
            os.remove(filepath)
            return False
        return True


def _is_window_on_screen(left: int, top: int, right: int, bottom: int) -> bool:
    """Check if a window rect has any visible portion on the primary monitor."""
    # Windows places minimized windows at (-32000, -32000) or similar off-screen coords
    if left < -10000 or top < -10000:
        return False
    if right <= left or bottom <= top:
        return False
    return True


def capture_screenshot(monitor: int = 1) -> str | None:
    """Capture a full-screen screenshot. Returns file path or None on failure.

    monitor=1 captures the primary display (mss.monitors[1]).
    monitor=0 captures all monitors combined (mss.monitors[0]).
    """
    try:
        import mss
    except ImportError:
        raise ScreenshotError("mss not installed. Run: pip install mss")

    ensure_screenshot_dir()
    filepath = os.path.join(SCREENSHOT_DIR, f"fullscreen_{_timestamp()}.png")

    try:
        with mss.MSS() as sct:
            monitors = sct.monitors
            if monitor >= len(monitors):
                monitor = len(monitors) - 1
            shot = sct.grab(monitors[monitor])
            import numpy as np
            full_img = np.array(shot)
            if not _save_cropped_image(full_img, 0, 0, full_img.shape[1], full_img.shape[0], filepath):
                raise ScreenshotError("Failed to save fullscreen screenshot")
            logger.info(f"Fullscreen screenshot saved: {filepath}")
            return filepath
    except ScreenshotError:
        raise
    except Exception as e:
        raise ScreenshotError(f"Fullscreen screenshot failed: {e}") from e


def capture_active_window() -> str | None:
    """Capture the currently focused application window. Returns file path or None."""
    try:
        import ctypes
        import ctypes.wintypes
    except ImportError:
        return _capture_active_window_linux()

    ensure_screenshot_dir()
    filepath = os.path.join(SCREENSHOT_DIR, f"window_{_timestamp()}.png")

    try:
        user32 = ctypes.windll.user32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            raise ScreenshotError("No foreground window found")

        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

        title_len = user32.GetWindowTextLengthW(hwnd) + 1
        title_buf = ctypes.create_unicode_buffer(title_len)
        user32.GetWindowTextW(hwnd, title_buf, title_len)
        window_title = title_buf.value

        # Always convert logical coords to physical pixels
        left, top, right, bottom = _logical_to_physical_rect(
            rect.left, rect.top, rect.right, rect.bottom
        )

        if not _is_window_on_screen(left, top, right, bottom):
            raise ScreenshotError(f"Window '{window_title}' is off-screen or minimized")

        import mss
        with mss.MSS() as sct:
            full_shot = sct.grab(sct.monitors[1])

            import numpy as np
            full_img = np.array(full_shot)
            if not _save_cropped_image(full_img, left, top, right, bottom, filepath):
                raise ScreenshotError("Failed to save window screenshot")
            logger.info(f"Window screenshot saved ({window_title}): {filepath}")
            return filepath
    except ScreenshotError:
        raise
    except Exception as e:
        raise ScreenshotError(f"Window screenshot failed: {e}") from e


def _get_linux_dpi_scale() -> tuple[float, float]:
    """Get the DPI scaling factor on Linux using xrandr.

    Returns (scale_x, scale_y). Falls back to (1.0, 1.0) if detection fails.
    On most X11 desktops without fractional scaling, this returns (1.0, 1.0).
    """
    import subprocess
    try:
        result = subprocess.run(
            ["xrandr", "--query"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return 1.0, 1.0
        for line in result.stdout.splitlines():
            if " connected" not in line:
                continue
            # Look for --scale NxN in the current mode line or transform matrix
            # xrandr reports: "eDP-1 connected primary 1920x1080+0+0 (normal left inverted right x-axis y-axis) ..."
            # Scale can appear as transform or --scale; parse the geometry instead.
            # Alternative: parse "current X x Y" from xrandr output header
            break
        # Parse the screen size line: "Screen 0: minimum ... current 1920 x 1080 ..."
        for line in result.stdout.splitlines():
            if line.startswith("Screen ") and "current" in line:
                import re
                m = re.search(r"current (\d+) x (\d+)", line)
                if m:
                    # Compare with the physical resolution from mss
                    pass
        return 1.0, 1.0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 1.0, 1.0


def _linux_logical_to_physical(left: int, top: int, right: int, bottom: int) -> tuple[int, int, int, int]:
    """Convert logical-pixel window rect to physical pixels on Linux.

    On X11 with HiDPI scaling (e.g. 200%), xdotool returns logical coordinates
    while mss captures in physical pixels. This function uses mss monitor info
    to detect the scale factor and convert.
    """
    try:
        import mss
        with mss.MSS() as sct:
            # mss.monitors[0] is the full virtual screen area (logical or physical depending on setup)
            # mss.monitors[1] is the primary monitor
            if len(sct.monitors) < 2:
                return left, top, right, bottom
            primary = sct.monitors[1]
            phys_w = primary["width"]
            phys_h = primary["height"]

        # Get logical resolution from xdpyinfo or xrandr
        import subprocess
        try:
            result = subprocess.run(
                ["xdpyinfo"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                import re
                for line in result.stdout.splitlines():
                    if "dimensions:" in line:
                        m = re.search(r"dimensions:\s+(\d+)x(\d+)", line)
                        if m:
                            log_w, log_h = int(m.group(1)), int(m.group(2))
                            scale_x = phys_w / log_w if log_w else 1.0
                            scale_y = phys_h / log_h if log_h else 1.0
                            if scale_x == 1.0 and scale_y == 1.0:
                                return left, top, right, bottom
                            return (
                                int(left * scale_x), int(top * scale_y),
                                int(right * scale_x), int(bottom * scale_y),
                            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return left, top, right, bottom
    except Exception:
        return left, top, right, bottom


def _capture_active_window_linux() -> str | None:
    """Capture active window on Linux using xdotool + mss."""
    if not _check_xdotool():
        raise ScreenshotError("xdotool not installed. Install: sudo apt install xdotool")
    try:
        import subprocess
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowgeometry"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            raise ScreenshotError("xdotool failed to get active window geometry")

        # Parse output: Position: X,Y (WxH)
        left = top = width = height = 0
        for line in result.stdout.splitlines():
            if "Position" in line:
                parts = line.split()
                pos = parts[1].split(",")
                geom = parts[2].replace("(", "").replace(")", "").split("x")
                left, top = int(pos[0]), int(pos[1])
                width, height = int(geom[0]), int(geom[1])
                break

        # Convert logical coords to physical pixels for mss
        left, top, right, bottom = _linux_logical_to_physical(
            left, top, left + width, top + height,
        )

        import mss
        ensure_screenshot_dir()
        filepath = os.path.join(SCREENSHOT_DIR, f"window_{_timestamp()}.png")
        with mss.MSS() as sct:
            monitor = {"left": left, "top": top, "width": right - left, "height": bottom - top}
            shot = sct.grab(monitor)
            mss.tools.to_png(shot.rgb, shot.size, output=filepath)
            return filepath
    except ScreenshotError:
        raise
    except Exception as e:
        raise ScreenshotError(f"Linux window screenshot failed: {e}") from e


def _get_dpi_scale() -> tuple[float, float]:
    """Get the DPI scaling factor (physical / logical) for the primary monitor.

    Works regardless of whether the process has DPI awareness set.
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hdc = user32.GetDC(0)
        logical_w = gdi32.GetDeviceCaps(hdc, 8)   # HORZRES
        logical_h = gdi32.GetDeviceCaps(hdc, 10)  # VERTRES
        phys_w = gdi32.GetDeviceCaps(hdc, 118)    # DESKTOPHORZRES
        phys_h = gdi32.GetDeviceCaps(hdc, 117)    # DESKTOPVERTRES
        user32.ReleaseDC(0, hdc)
        scale_x = phys_w / logical_w if logical_w else 1.0
        scale_y = phys_h / logical_h if logical_h else 1.0
        return scale_x, scale_y
    except Exception:
        return 1.0, 1.0


def _logical_to_physical_rect(left: int, top: int, right: int, bottom: int) -> tuple[int, int, int, int]:
    """Convert logical-pixel window rect to physical pixels using DPI scale."""
    scale_x, scale_y = _get_dpi_scale()
    if scale_x == 1.0 and scale_y == 1.0:
        return left, top, right, bottom
    return int(left * scale_x), int(top * scale_y), int(right * scale_x), int(bottom * scale_y)


def list_windows() -> list[dict]:
    """List visible application windows with titles and positions.

    Only returns windows that are on-screen (not minimized/hidden).
    Returns coordinates in physical pixels (converted from logical via DPI scale).
    """
    try:
        import ctypes
        import ctypes.wintypes
    except ImportError:
        return _list_windows_linux()

    windows = []
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        def _enum_cb(hwnd, lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            title_len = user32.GetWindowTextLengthW(hwnd)
            if title_len == 0:
                return True
            title_buf = ctypes.create_unicode_buffer(title_len + 1)
            user32.GetWindowTextW(hwnd, title_buf, title_len + 1)
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            # Convert logical coords to physical pixels
            p_left, p_top, p_right, p_bottom = _logical_to_physical_rect(
                rect.left, rect.top, rect.right, rect.bottom
            )
            # Skip minimized/off-screen windows
            if not _is_window_on_screen(p_left, p_top, p_right, p_bottom):
                return True
            windows.append({
                "hwnd": hwnd,
                "title": title_buf.value,
                "left": p_left,
                "top": p_top,
                "width": p_right - p_left,
                "height": p_bottom - p_top,
            })
            return True

        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
    except Exception as e:
        logger.error(f"Failed to list windows: {e}")
    return windows


def _check_xdotool() -> bool:
    """Check if xdotool is available on Linux. Log a warning if not."""
    import subprocess
    try:
        subprocess.run(["xdotool", "--version"], capture_output=True, timeout=2)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("xdotool not installed. Window capture on Linux requires xdotool. Install: sudo apt install xdotool")
        return False


def _list_windows_linux() -> list[dict]:
    if not _check_xdotool():
        return []
    try:
        import subprocess
        result = subprocess.run(
            ["xdotool", "search", "--name", ""],
            capture_output=True, text=True, timeout=5,
        )
        windows = []
        for wid in result.stdout.strip().splitlines():
            wid = wid.strip()
            if not wid:
                continue
            name_result = subprocess.run(
                ["xdotool", "getwindowname", wid],
                capture_output=True, text=True, timeout=2,
            )
            if name_result.returncode == 0 and name_result.stdout.strip():
                windows.append({"hwnd": int(wid), "title": name_result.stdout.strip()})
        return windows
    except Exception:
        return []


def capture_window_by_hwnd(hwnd: int) -> str | None:
    """Capture a specific window by its HWND (Windows) or window ID (Linux).

    More reliable than title-based capture because window titles can change
    dynamically (e.g. Claude Code spinner animation).
    """
    try:
        import ctypes
        import ctypes.wintypes
    except ImportError:
        return _capture_window_by_wid_linux(hwnd)

    try:
        user32 = ctypes.windll.user32

        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            raise ScreenshotError(f"HWND {hwnd} is not a visible window")

        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

        title_len = user32.GetWindowTextLengthW(hwnd) + 1
        title_buf = ctypes.create_unicode_buffer(title_len)
        user32.GetWindowTextW(hwnd, title_buf, title_len)
        window_title = title_buf.value

        left, top, right, bottom = _logical_to_physical_rect(
            rect.left, rect.top, rect.right, rect.bottom
        )

        if not _is_window_on_screen(left, top, right, bottom):
            raise ScreenshotError(f"Window '{window_title}' (HWND {hwnd}) is off-screen or minimized")

        ensure_screenshot_dir()
        filepath = os.path.join(SCREENSHOT_DIR, f"window_{_timestamp()}.png")

        import mss
        with mss.MSS() as sct:
            full_shot = sct.grab(sct.monitors[1])

            import numpy as np
            full_img = np.array(full_shot)
            if not _save_cropped_image(full_img, left, top, right, bottom, filepath):
                raise ScreenshotError("Failed to save window screenshot")
            logger.info(f"Window screenshot saved ({window_title}): {filepath}")
            return filepath
    except ScreenshotError:
        raise
    except Exception as e:
        raise ScreenshotError(f"Window-by-HWND screenshot failed: {e}") from e


def capture_window_by_title(title: str) -> str | None:
    """Capture a specific window by matching its title (partial match).

    Prefer capture_window_by_hwnd() when the HWND is known, as window titles
    can change dynamically (e.g. spinner animations).
    """
    try:
        import ctypes
        import ctypes.wintypes
    except ImportError:
        return _capture_window_by_title_linux(title)

    try:
        windows = list_windows()
        matches = [w for w in windows if title.lower() in w["title"].lower()]
        if not matches:
            visible_titles = [w["title"] for w in windows[:10]]
            raise ScreenshotError(f"No window matching '{title}' found. Visible windows: {visible_titles}")

        # Use the first match's HWND for reliable capture
        target = matches[0]
        return capture_window_by_hwnd(target["hwnd"])
    except ScreenshotError:
        raise
    except Exception as e:
        raise ScreenshotError(f"Window-by-title screenshot failed: {e}") from e


def _capture_window_by_wid_linux(wid: int) -> str | None:
    """Capture a specific window by its window ID on Linux using xdotool + mss."""
    if not _check_xdotool():
        raise ScreenshotError("xdotool not installed. Install: sudo apt install xdotool")
    try:
        import subprocess
        geom_result = subprocess.run(
            ["xdotool", "getwindowgeometry", str(wid)],
            capture_output=True, text=True, timeout=5,
        )
        if geom_result.returncode != 0:
            raise ScreenshotError(f"Window ID {wid} not found")

        left = top = width = height = 0
        for line in geom_result.stdout.splitlines():
            if "Position" in line:
                parts = line.split()
                pos = parts[1].split(",")
                geom = parts[2].replace("(", "").replace(")", "").split("x")
                left, top = int(pos[0]), int(pos[1])
                width, height = int(geom[0]), int(geom[1])

        if width <= 0 or height <= 0:
            raise ScreenshotError(f"Invalid window dimensions for WID {wid}: {width}x{height}")

        # Convert logical coords to physical pixels for mss
        p_left, p_top, p_right, p_bottom = _linux_logical_to_physical(
            left, top, left + width, top + height,
        )
        p_width = p_right - p_left
        p_height = p_bottom - p_top

        import mss
        ensure_screenshot_dir()
        filepath = os.path.join(SCREENSHOT_DIR, f"window_{_timestamp()}.png")
        with mss.MSS() as sct:
            monitor = {"left": p_left, "top": p_top, "width": p_width, "height": p_height}
            shot = sct.grab(monitor)
            mss.tools.to_png(shot.rgb, shot.size, output=filepath)
            return filepath
    except ScreenshotError:
        raise
    except Exception as e:
        raise ScreenshotError(f"Linux window-by-WID screenshot failed: {e}") from e


def _capture_window_by_title_linux(title: str) -> str | None:
    if not _check_xdotool():
        raise ScreenshotError("xdotool not installed. Install: sudo apt install xdotool")
    try:
        import subprocess
        result = subprocess.run(
            ["xdotool", "search", "--name", title],
            capture_output=True, text=True, timeout=5,
        )
        wid = result.stdout.strip().splitlines()[0]
        geom_result = subprocess.run(
            ["xdotool", "getwindowgeometry", wid],
            capture_output=True, text=True, timeout=5,
        )
        left = top = width = height = 0
        for line in geom_result.stdout.splitlines():
            if "Position" in line:
                parts = line.split()
                pos = parts[1].split(",")
                geom = parts[2].replace("(", "").replace(")", "").split("x")
                left, top = int(pos[0]), int(pos[1])
                width, height = int(geom[0]), int(geom[1])

        # Convert logical coords to physical pixels for mss
        p_left, p_top, p_right, p_bottom = _linux_logical_to_physical(
            left, top, left + width, top + height,
        )
        p_width = p_right - p_left
        p_height = p_bottom - p_top

        import mss
        ensure_screenshot_dir()
        filepath = os.path.join(SCREENSHOT_DIR, f"window_{_timestamp()}.png")
        with mss.MSS() as sct:
            monitor = {"left": p_left, "top": p_top, "width": p_width, "height": p_height}
            shot = sct.grab(monitor)
            mss.tools.to_png(shot.rgb, shot.size, output=filepath)
            return filepath
    except ScreenshotError:
        raise
    except Exception as e:
        raise ScreenshotError(f"Linux window-by-title screenshot failed: {e}") from e


def list_screenshots() -> list[str]:
    """List all saved screenshots, newest first."""
    ensure_screenshot_dir()
    files = sorted(
        Path(SCREENSHOT_DIR).glob("*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [str(f) for f in files]


def cleanup_old_screenshots(max_age_hours: int = 24):
    """Remove screenshots older than max_age_hours."""
    ensure_screenshot_dir()
    cutoff = time.time() - max_age_hours * 3600
    for f in Path(SCREENSHOT_DIR).glob("*.png"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            logger.info(f"Cleaned up old screenshot: {f}")