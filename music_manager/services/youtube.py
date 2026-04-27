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

_COOKIES_NEEDED_PATTERNS = [
    "sign in to confirm",
    "confirm you're not a bot",
]

_RATE_LIMIT_PATTERNS = [
    "http error 429",
    "too many requests",
]

_SAFARI_COOKIES_PATH = os.path.expanduser("~/Library/Cookies/Cookies.binarycookies")


@dataclass
class _SearchOutcome:
    """Result of a single yt-dlp search invocation."""

    candidates: list[dict] = field(default_factory=list)
    is_rate_limited: bool = False
    needs_cookies: bool = False
    error: str = ""
    returncode: int = 0

# ── Rate limit state (module-level, thread-safe) ────────────────────────────

_lock = threading.Lock()
_consecutive_fails: int = 0
_last_search_ts: float = 0.0
_rate_limit_callback: Callable[[int, str], None] | None = None
_cookies_callback: Callable[[], bool] | None = None
_use_cookies: bool = False
_cookies_decided: bool = False


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


def set_cookies_callback(callback: Callable[[], bool] | None) -> None:
    """Register a callback invoked when cookies are needed (age-gate, bot-confirm).

    The callback handles the full UI interaction (check login, prompt user,
    open Safari if needed) and returns True if cookies should be used.
    Pass None to unregister.
    """
    global _cookies_callback  # noqa: PLW0603
    _cookies_callback = callback


def set_use_cookies(value: bool) -> None:
    """Set whether to use Safari cookies for yt-dlp (loaded from config)."""
    global _use_cookies, _cookies_decided  # noqa: PLW0603
    _use_cookies = value
    _cookies_decided = value


def get_use_cookies() -> bool:
    """Return whether Safari cookies are currently active."""
    return _use_cookies


def check_safari_youtube_login() -> bool:
    """Check if Safari has YouTube auth cookies (heuristic, local file read)."""
    try:
        with open(_SAFARI_COOKIES_PATH, "rb") as fh:
            data = fh.read()
        return b".youtube.com" in data and b"LOGIN_INFO" in data
    except (OSError, PermissionError):
        return False


# ── Entry point ──────────────────────────────────────────────────────────────


def search_by_isrc(isrc: str) -> list[dict]:
    """Search YouTube by ISRC. Returns candidates sorted by Topic channel first.

    Each candidate: {id, title, url, duration, channel}.
    Applies adaptive throttle to avoid YouTube rate limiting.
    Distinguishes "not found" / "cookies needed" / "rate-limit" / "error".
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
    if (
        not outcome.is_rate_limited
        and not outcome.needs_cookies
        and outcome.returncode == 0
        and not outcome.error
    ):
        log_event("youtube_search", isrc=isrc, results=0, duration_ms=0)
        return []

    # Cookies needed (age-gate, bot-confirm) → prompt user once per session
    if outcome.needs_cookies:
        return _handle_cookies_needed(isrc)

    # Rate-limit or yt-dlp error → exponential backoff
    backoff = _record_fail()
    reason = outcome.error[:200] or "YouTube error"

    _notify_rate_limit(backoff, reason)
    _sleep_backoff(backoff)

    # Retry once after backoff
    retry = _do_search(isrc)
    if retry.candidates:
        _record_success()
        return retry.candidates
    if retry.needs_cookies:
        return _handle_cookies_needed(isrc)

    log_event("youtube_search", isrc=isrc, results=0, retried=True, duration_ms=0)
    return []


def download_track(url: str, output_dir: str) -> tuple[str, int | None]:
    """Download a YouTube audio as M4A. Returns (filepath, duration).

    Raises RuntimeError on failure.
    """
    os.makedirs(output_dir, exist_ok=True)
    output_template = os.path.join(output_dir, "%(id)s.%(ext)s")
    t0 = time.monotonic()

    cmd = ["yt-dlp"]
    if _use_cookies:
        cmd.extend(["--cookies-from-browser", "safari"])
    cmd.extend([
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
    ])

    try:
        result = subprocess.run(
            cmd,
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
    cmd = ["yt-dlp"]
    if _use_cookies:
        cmd.extend(["--cookies-from-browser", "safari"])
    cmd.extend([
        "--dump-json",
        "--skip-download",
        "--no-playlist",
        "--quiet",
        f"ytsearch1:{isrc}",
    ])
    try:
        result = subprocess.run(
            cmd,
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

    # Non-zero exit → classify error
    if result.returncode != 0:
        cookies_needed = _detect_cookies_needed(stderr)
        rate_limited = not cookies_needed and _detect_rate_limit(stderr)
        duration_ms = int((time.monotonic() - t0) * 1000)
        log_event(
            "youtube_search",
            isrc=isrc,
            results=0,
            duration_ms=duration_ms,
            error=stderr[:200],
            rate_limited=rate_limited,
            cookies_needed=cookies_needed,
        )
        return _SearchOutcome(
            is_rate_limited=rate_limited,
            needs_cookies=cookies_needed,
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


def _detect_cookies_needed(stderr: str) -> bool:
    """Check if stderr indicates cookies are needed (age-gate, bot-confirm)."""
    lower = stderr.lower()
    return any(pattern in lower for pattern in _COOKIES_NEEDED_PATTERNS)


def _detect_rate_limit(stderr: str) -> bool:
    """Check if stderr contains YouTube rate-limit signals (HTTP 429)."""
    lower = stderr.lower()
    return any(pattern in lower for pattern in _RATE_LIMIT_PATTERNS)


def _handle_cookies_needed(isrc: str) -> list[dict]:
    """Handle a search that needs cookies. Prompts user once per session."""
    global _use_cookies, _cookies_decided  # noqa: PLW0603

    # Already declined this session → skip immediately
    if _cookies_decided and not _use_cookies:
        log_event("youtube_search", isrc=isrc, results=0, duration_ms=0,
                  reason="cookies_declined")
        return []

    # Cookies were active but still blocked → expired, reset config
    if _use_cookies:
        _use_cookies = False
        _cookies_decided = False
        from music_manager.core.config import save_config  # noqa: PLC0415

        save_config({"youtube_cookies": False})

    # Ask user via UI callback
    cb = _cookies_callback
    if not cb:
        log_event("youtube_search", isrc=isrc, results=0, duration_ms=0,
                  reason="age_restricted")
        return []

    activated = cb()  # blocks until UI responds
    _cookies_decided = True
    _use_cookies = activated

    if not activated:
        log_event("youtube_search", isrc=isrc, results=0, duration_ms=0,
                  reason="cookies_declined")
        return []

    # Persist for future sessions
    from music_manager.core.config import save_config  # noqa: PLC0415

    save_config({"youtube_cookies": True})

    # Retry search with cookies now active
    retry = _do_search(isrc)
    if retry.candidates:
        _record_success()
        return retry.candidates

    # Cookies didn't help → disable so we don't re-prompt every track
    _use_cookies = False

    log_event("youtube_search", isrc=isrc, results=0, duration_ms=0,
              reason="cookies_failed")
    return []


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
