#!/usr/bin/env python3
"""CLI entry point for screenshot capture. Used by AI agents via Bash.

Usage:
    vibe-screenshot                  Capture full screen
    vibe-screenshot fullscreen       Capture full screen
    vibe-screenshot window           Capture active (foreground) window
    vibe-screenshot title <title>    Capture window by title (partial match)
    vibe-screenshot hwnd <hwnd>      Capture window by HWND/window ID
    vibe-screenshot list             List visible windows (hwnd + title)

Prints the saved file path on success, or an error message on failure.
"""

import sys
import os

# Ensure the vibe-remote package is importable regardless of CWD
_vibe_remote_dir = os.path.dirname(os.path.abspath(__file__))
if _vibe_remote_dir not in sys.path:
    sys.path.insert(0, _vibe_remote_dir)

from modules.tools.screenshot import (
    capture_screenshot,
    capture_active_window,
    capture_window_by_title,
    capture_window_by_hwnd,
    list_windows,
    ScreenshotError,
)


def main():
    args = sys.argv[1:]

    if not args or args[0] == "fullscreen":
        path = capture_screenshot()
    elif args[0] == "window":
        path = capture_active_window()
    elif args[0] == "title" and len(args) >= 2:
        title = " ".join(args[1:])
        path = capture_window_by_title(title)
    elif args[0] == "hwnd" and len(args) >= 2:
        hwnd = int(args[1])
        path = capture_window_by_hwnd(hwnd)
    elif args[0] == "list":
        windows = list_windows()
        for w in windows:
            title = w.get("title", "")
            if title:
                try:
                    print(f"{w['hwnd']}\t{title}")
                except UnicodeEncodeError:
                    safe_title = title.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
                    sys.stdout.buffer.write(f"{w['hwnd']}\t{safe_title}\n".encode("utf-8", errors="replace"))
        return
    else:
        print(f"Unknown command: {args}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    if path:
        print(path)
    else:
        print("Screenshot capture failed", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except ScreenshotError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
