"""Startup checks — verify system requirements before launching.

Each function is independent and side-effect free. The caller decides
what to do with the results (display errors, propose install, etc.).
"""

import os
import shutil
import subprocess
import sys
from datetime import date, datetime

# ── Constants ────────────────────────────────────────────────────────────────

# yt-dlp releases track YouTube's serving changes. Past ~6 weeks the download
# endpoints start answering "HTTP Error 403: Forbidden" — the single biggest
# cause of import failures observed in the field. yt-dlp itself warns at 90
# days, which is far too late to be useful.
YT_DLP_MAX_AGE_DAYS = 45

_YT_DLP_VERSION_TIMEOUT = 10

# Where a second yt-dlp commonly lives. Homebrew's formula regularly trails
# upstream by weeks while `uv tool` / `pipx` installs track releases, and a
# machine often ends up with both.
_YT_DLP_CANDIDATE_DIRS = (
    os.path.join(os.path.expanduser("~"), ".local", "bin"),
    "/opt/homebrew/bin",
    "/opt/local/bin",
    "/usr/local/bin",
)

# Resolved once per process — each probe costs a subprocess.
_yt_dlp_path: str | None = None


# ── Entry point ──────────────────────────────────────────────────────────────


def check_macos() -> bool:
    """Return True if running on macOS."""
    return sys.platform == "darwin"


def check_dependencies() -> list[str]:
    """Return list of missing system dependencies (empty = all present)."""
    required = ["afplay", "yt-dlp", "ffmpeg"]
    return [dep for dep in required if shutil.which(dep) is None]


def yt_dlp_path() -> str:
    """Return the path of the **newest** yt-dlp on this machine ("" if none).

    Resolving by PATH order alone isn't enough: a user can have a current
    yt-dlp in ``~/.local/bin`` and an outdated Homebrew one in
    ``/opt/homebrew/bin``, with Homebrew earlier in PATH. That silently pins
    the app to a build YouTube already answers 403 to — which is exactly the
    situation the field logs showed.
    """
    global _yt_dlp_path  # noqa: PLW0603
    if _yt_dlp_path is not None:
        return _yt_dlp_path

    on_path = shutil.which("yt-dlp") or ""
    candidates: list[str] = []
    seen: set[str] = set()
    for candidate in (on_path, *(os.path.join(d, "yt-dlp") for d in _YT_DLP_CANDIDATE_DIRS)):
        if not candidate or not os.path.isfile(candidate):
            continue
        key = os.path.realpath(candidate)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)

    best, best_age = "", None
    for candidate in candidates:
        age = yt_dlp_age_days(_probe_version(candidate))
        if age < 0:
            continue  # unparseable version — can't rank it
        if best_age is None or age < best_age:
            best, best_age = candidate, age

    # Nothing rankable → keep whatever PATH says (or the first one found).
    _yt_dlp_path = best or on_path or (candidates[0] if candidates else "")
    return _yt_dlp_path


def yt_dlp_version() -> str:
    """Return the version of the yt-dlp the app will actually run ("" if none)."""
    path = yt_dlp_path()
    return _probe_version(path) if path else ""


def yt_dlp_age_days(version: str, today: date | None = None) -> int:
    """Return the age in days of a ``YYYY.MM.DD`` yt-dlp version.

    Returns ``-1`` when the version string can't be parsed (nightly builds,
    source checkouts, empty string) — callers must treat that as "unknown",
    never as "stale".
    """
    head = (version or "").strip().split("-")[0]
    try:
        released = datetime.strptime(head, "%Y.%m.%d").date()
    except ValueError:
        return -1
    reference = today or date.today()
    return max(0, (reference - released).days)


def yt_dlp_update_hint() -> str:
    """Return the command that actually updates this yt-dlp install.

    ``yt-dlp -U`` refuses to run on a Homebrew install, so telling every user
    the same thing sends half of them down a dead end.
    """
    path = yt_dlp_path()
    # Follow the symlink: `uv tool` and `pipx` both expose a shim in
    # ~/.local/bin whose name says nothing about how to update it.
    real = os.path.realpath(path) if path else ""
    if "/uv/tools/" in real:
        return "uv tool upgrade yt-dlp"
    if "/pipx/" in real:
        return "pipx upgrade yt-dlp"
    if "/Cellar/" in real or "/homebrew/" in real:
        return "brew upgrade yt-dlp"
    if "/.venv/" in real or "/site-packages/" in real:
        return "pip install -U yt-dlp"
    return "yt-dlp -U"


def _probe_version(executable: str) -> str:
    """Run ``<executable> --version`` and return its output ("" on any failure)."""
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=_YT_DLP_VERSION_TIMEOUT,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    return result.stdout.strip().splitlines()[0].strip()


def check_yt_dlp_fresh(max_age_days: int = YT_DLP_MAX_AGE_DAYS) -> tuple[str, int, bool]:
    """Return ``(version, age_days, is_stale)`` for the installed yt-dlp.

    ``age_days`` is ``-1`` and ``is_stale`` is False when the version can't
    be determined — an unknown version must never block an import.
    """
    version = yt_dlp_version()
    age = yt_dlp_age_days(version)
    return version, age, age > max_age_days


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
