"""YouTube audio search and download via yt-dlp.

Searches by ISRC (returns Topic channels = official label audio).
Downloads best audio as M4A.
Adaptive throttle: detects YouTube rate limiting and backs off automatically.
"""

import glob
import json
import os
import subprocess
import threading
import time
from collections.abc import Callable

from music_manager.core.logger import log_event

# ── Constants ────────────────────────────────────────────────────────────────

_SEARCH_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 120

_MIN_SEARCH_INTERVAL = 12.0  # seconds between searches (~5/min)
_BACKOFF_THRESHOLD_WARN = 3  # consecutive fails → short pause
_BACKOFF_THRESHOLD_HARD = 5  # consecutive fails → long pause
_BACKOFF_SHORT = 30  # seconds
_BACKOFF_LONG = 120  # seconds

# ── Rate limit state (module-level, thread-safe) ────────────────────────────

_lock = threading.Lock()
_consecutive_fails: int = 0
_last_search_ts: float = 0.0
_rate_limit_callback: Callable[[int], None] | None = None


# ── Public API ──────────────────────────────────────────────────────────────


def set_rate_limit_callback(callback: Callable[[int], None] | None) -> None:
    """Register a callback invoked when rate limiting is detected.

    The callback receives the number of seconds the throttle will wait.
    Pass None to unregister.
    """
    global _rate_limit_callback  # noqa: PLW0603
    _rate_limit_callback = callback


def reset_throttle() -> None:
    """Reset throttle state (e.g. at start of a new batch)."""
    global _consecutive_fails, _last_search_ts  # noqa: PLW0603
    with _lock:
        _consecutive_fails = 0
        _last_search_ts = 0.0


# ── Entry point ──────────────────────────────────────────────────────────────


def search_by_isrc(isrc: str) -> list[dict]:
    """Search YouTube by ISRC. Returns candidates sorted by Topic channel first.

    Each candidate: {id, title, url, duration, channel}.
    Applies adaptive throttle to avoid YouTube rate limiting.
    """
    if not isrc:
        return []

    _throttle_wait()
    candidates = _do_search(isrc)

    if candidates:
        _record_success()
        return candidates

    # No results — possible rate limit. Check if backoff needed.
    backoff = _record_fail()
    if backoff > 0:
        # Rate limit detected — wait and retry once
        _notify_rate_limit(backoff)
        _sleep_backoff(backoff)
        candidates = _do_search(isrc)
        if candidates:
            _record_success()
            return candidates
        # Still no results after retry — genuinely absent
        log_event("youtube_search", isrc=isrc, results=0, retried=True, duration_ms=0)
        return []

    log_event("youtube_search", isrc=isrc, results=0, duration_ms=0)
    return []


def download_track(url: str, output_dir: str) -> tuple[str, int | None]:
    """Download a YouTube audio as M4A. Returns (filepath, duration).

    Raises RuntimeError on failure.
    """
    os.makedirs(output_dir, exist_ok=True)
    output_template = os.path.join(output_dir, "%(id)s.%(ext)s")
    t0 = time.monotonic()

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--format",
                "bestaudio",
                "--extract-audio",
                "--audio-format",
                "m4a",
                "--audio-quality",
                "0",
                "--output",
                output_template,
                "--no-playlist",
                "--quiet",
                "--print",
                "after_move:filepath",
                "--print",
                "after_move:duration",
                "--",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=_DOWNLOAD_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _cleanup_partial(output_dir)
        log_event("youtube_download_failed", url=url, reason="timeout", duration_ms=duration_ms)
        raise RuntimeError(f"yt-dlp timeout after {_DOWNLOAD_TIMEOUT}s") from None

    if result.returncode != 0:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _cleanup_partial(output_dir)
        stderr = result.stderr.strip()
        log_event("youtube_download_failed", url=url, reason=stderr[:200], duration_ms=duration_ms)
        raise RuntimeError(f"yt-dlp error: {stderr}") from None

    filepath, duration = _parse_output(result.stdout)
    if filepath and os.path.exists(filepath):
        duration_ms = int((time.monotonic() - t0) * 1000)
        filesize = os.path.getsize(filepath)
        log_event("youtube_download", url=url, duration_ms=duration_ms, filesize=filesize)
        return filepath, duration

    filepath = _find_latest_m4a(output_dir)
    if filepath:
        duration_ms = int((time.monotonic() - t0) * 1000)
        filesize = os.path.getsize(filepath)
        log_event("youtube_download", url=url, duration_ms=duration_ms, filesize=filesize)
        return filepath, duration

    duration_ms = int((time.monotonic() - t0) * 1000)
    log_event("youtube_download_failed", url=url, reason="file_not_found", duration_ms=duration_ms)
    raise RuntimeError("Audio file not found after download")


# ── Private Functions ────────────────────────────────────────────────────────


def _do_search(isrc: str) -> list[dict]:
    """Execute a single yt-dlp search. Returns candidates list."""
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--dump-json",
                "--skip-download",
                "--no-playlist",
                "--quiet",
                f"ytsearch1:{isrc}",
            ],
            capture_output=True,
            text=True,
            timeout=_SEARCH_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log_event("youtube_search", isrc=isrc, results=0, duration_ms=duration_ms, timeout=True)
        return []

    candidates = []
    for line in result.stdout.strip().splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidates.append(
            {
                "id": data.get("id", ""),
                "title": data.get("title", ""),
                "url": data.get("webpage_url", ""),
                "duration": data.get("duration") or 0,
                "channel": data.get("channel", ""),
            }
        )

    candidates.sort(
        key=lambda candidate: 0 if "topic" in candidate["channel"].lower() else 1,
    )

    duration_ms = int((time.monotonic() - t0) * 1000)
    if candidates:
        log_event("youtube_search", isrc=isrc, results=len(candidates), duration_ms=duration_ms)
    return candidates


def _throttle_wait() -> None:
    """Enforce minimum interval between searches."""
    global _last_search_ts  # noqa: PLW0603
    with _lock:
        now = time.monotonic()
        elapsed = now - _last_search_ts
        if _last_search_ts > 0 and elapsed < _MIN_SEARCH_INTERVAL:
            wait = _MIN_SEARCH_INTERVAL - elapsed
        else:
            wait = 0
        _last_search_ts = now + max(wait, 0)

    if wait > 0:
        time.sleep(wait)


def _record_success() -> None:
    """Reset consecutive fail counter on success."""
    global _consecutive_fails  # noqa: PLW0603
    with _lock:
        _consecutive_fails = 0


def _record_fail() -> int:
    """Increment fail counter. Returns backoff seconds (0 = no backoff yet)."""
    global _consecutive_fails  # noqa: PLW0603
    with _lock:
        _consecutive_fails += 1
        fails = _consecutive_fails

    if fails >= _BACKOFF_THRESHOLD_HARD:
        backoff = _BACKOFF_LONG
    elif fails >= _BACKOFF_THRESHOLD_WARN:
        backoff = _BACKOFF_SHORT
    else:
        backoff = 0

    if backoff > 0:
        log_event(
            "youtube_rate_limit",
            consecutive_fails=fails,
            backoff_seconds=backoff,
        )
    return backoff


def _notify_rate_limit(seconds: int) -> None:
    """Notify UI callback about rate limit wait."""
    cb = _rate_limit_callback
    if cb:
        try:
            cb(seconds)
        except Exception:  # noqa: BLE001
            pass


def _sleep_backoff(seconds: int) -> None:
    """Sleep for backoff period."""
    time.sleep(seconds)


def _parse_output(stdout: str) -> tuple[str, int | None]:
    """Parse yt-dlp output for filepath and duration."""
    lines = stdout.strip().splitlines()
    filepath = lines[0] if lines else ""
    duration = None
    if len(lines) >= 2:
        try:
            duration = int(lines[1])
        except ValueError:
            pass
    return filepath, duration


def _cleanup_partial(output_dir: str) -> None:
    """Remove .part files left by incomplete downloads."""
    for partial in glob.glob(os.path.join(output_dir, "*.part")):
        try:
            os.remove(partial)
        except OSError:
            pass


def _find_latest_m4a(output_dir: str) -> str:
    """Find the most recent M4A file in directory."""
    files = sorted(
        glob.glob(os.path.join(output_dir, "*.m4a")),
        key=os.path.getmtime,
        reverse=True,
    )
    return files[0] if files else ""
