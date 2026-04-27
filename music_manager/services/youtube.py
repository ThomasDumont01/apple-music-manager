"""YouTube audio search and download via yt-dlp.

Searches by ISRC (returns Topic channels = official label audio).
Downloads best audio as M4A.
Adaptive throttle: detects YouTube rate limiting and backs off automatically.
"""

import glob
import json
import os
import random
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from music_manager.core.logger import log_event

# ── Constants ────────────────────────────────────────────────────────────────

_SEARCH_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 120

_MIN_SEARCH_INTERVAL = 12.0  # seconds between searches (~5/min)
_SEARCH_JITTER = 3.0  # ±3s on interval → 9-15s range

_BACKOFF_BASE = 30  # starting backoff seconds
_BACKOFF_MAX = 1800  # cap at 30 minutes
_JITTER_FACTOR = 0.25  # ±25% jitter on backoff
_RATE_LIMIT_BACKOFF = 1800  # "Sign in to confirm" → 30 min

_RATE_LIMIT_PATTERNS = [
    "sign in to confirm",
    "http error 429",
    "too many requests",
    "confirm you're not a bot",
]


@dataclass
class _SearchOutcome:
    """Result of a single yt-dlp search invocation."""

    candidates: list[dict] = field(default_factory=list)
    is_rate_limited: bool = False
    error: str = ""
    returncode: int = 0

# ── Rate limit state (module-level, thread-safe) ────────────────────────────

_lock = threading.Lock()
_consecutive_fails: int = 0
_last_search_ts: float = 0.0
_rate_limit_callback: Callable[[int, str], None] | None = None


# ── Public API ──────────────────────────────────────────────────────────────


def set_rate_limit_callback(callback: Callable[[int, str], None] | None) -> None:
    """Register a callback invoked when rate limiting is detected.

    The callback receives (seconds_to_wait, reason_message).
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
    Distinguishes "not found" (no backoff) from actual errors (exponential backoff).
    """
    if not isrc:
        return []

    _throttle_wait()
    outcome = _do_search(isrc)

    # Clean results → success, reset fail counter
    if outcome.candidates:
        _record_success()
        return outcome.candidates

    # Clean search, 0 results, no error → track genuinely absent, no backoff
    if not outcome.is_rate_limited and outcome.returncode == 0 and not outcome.error:
        log_event("youtube_search", isrc=isrc, results=0, duration_ms=0)
        return []

    # Rate-limit (captcha/bot) → long backoff, skip _record_fail
    if outcome.is_rate_limited:
        backoff = _RATE_LIMIT_BACKOFF
        reason = outcome.error[:200] or "YouTube rate limit"
    else:
        # yt-dlp error → exponential backoff
        backoff = _record_fail()
        reason = outcome.error[:200] or "YouTube error"

    _notify_rate_limit(backoff, reason)
    _sleep_backoff(backoff)

    # Retry once after backoff
    retry = _do_search(isrc)
    if retry.candidates:
        _record_success()
        return retry.candidates

    log_event("youtube_search", isrc=isrc, results=0, retried=True, duration_ms=0)
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


def _do_search(isrc: str) -> _SearchOutcome:
    """Execute a single yt-dlp search. Returns outcome with error context."""
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
        return _SearchOutcome(error="timeout")

    stderr = result.stderr.strip()

    # Non-zero exit → check for rate-limit signals in stderr
    if result.returncode != 0:
        rate_limited = _detect_rate_limit(stderr)
        duration_ms = int((time.monotonic() - t0) * 1000)
        log_event(
            "youtube_search",
            isrc=isrc,
            results=0,
            duration_ms=duration_ms,
            error=stderr[:200],
            rate_limited=rate_limited,
        )
        return _SearchOutcome(
            is_rate_limited=rate_limited,
            error=stderr[:200],
            returncode=result.returncode,
        )

    # returncode == 0 → parse candidates
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
    return _SearchOutcome(candidates=candidates)


def _throttle_wait() -> None:
    """Enforce minimum interval between searches with jitter (9-15s)."""
    global _last_search_ts  # noqa: PLW0603
    jittered = _MIN_SEARCH_INTERVAL + random.uniform(-_SEARCH_JITTER, _SEARCH_JITTER)
    with _lock:
        now = time.monotonic()
        elapsed = now - _last_search_ts
        wait = max(0, jittered - elapsed) if _last_search_ts > 0 else 0
        _last_search_ts = now + wait

    if wait > 0:
        time.sleep(wait)


def _record_success() -> None:
    """Reset consecutive fail counter on success."""
    global _consecutive_fails  # noqa: PLW0603
    with _lock:
        _consecutive_fails = 0


def _record_fail() -> int:
    """Increment fail counter. Returns exponential backoff seconds with jitter."""
    global _consecutive_fails  # noqa: PLW0603
    with _lock:
        _consecutive_fails += 1
        fails = _consecutive_fails

    backoff = _compute_backoff(fails)
    log_event("youtube_rate_limit", consecutive_fails=fails, backoff_seconds=backoff)
    return backoff


def _compute_backoff(fails: int) -> int:
    """Exponential backoff: 30→60→120→…→1800 (cap) with ±25% jitter."""
    raw = _BACKOFF_BASE * (2 ** (fails - 1))
    capped = min(raw, _BACKOFF_MAX)
    jitter = random.uniform(1 - _JITTER_FACTOR, 1 + _JITTER_FACTOR)
    return int(capped * jitter)


def _detect_rate_limit(stderr: str) -> bool:
    """Check if stderr contains YouTube rate-limit signals."""
    lower = stderr.lower()
    return any(pattern in lower for pattern in _RATE_LIMIT_PATTERNS)


def _notify_rate_limit(seconds: int, reason: str = "") -> None:
    """Notify UI callback about rate limit wait."""
    cb = _rate_limit_callback
    if cb:
        try:
            cb(seconds, reason)
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
