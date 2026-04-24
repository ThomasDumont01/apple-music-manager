"""Startup checks — verify system requirements before launching.

Each function is independent and side-effect free. The caller decides
what to do with the results (display errors, propose install, etc.).
"""

import shutil
import subprocess
import sys

# ── Entry point ──────────────────────────────────────────────────────────────


def check_macos() -> bool:
    """Return True if running on macOS."""
    return sys.platform == "darwin"


def check_dependencies() -> list[str]:
    """Return list of missing system dependencies (empty = all present)."""
    required = ["afplay", "yt-dlp", "ffmpeg"]
    return [dep for dep in required if shutil.which(dep) is None]


def check_brew() -> bool:
    """Return True if Homebrew is installed."""
    return shutil.which("brew") is not None


def check_apple_music() -> bool:
    """Return True if Apple Music responds to AppleScript."""
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "Music" to name'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
