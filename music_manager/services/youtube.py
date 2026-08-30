"""YouTube audio search and download via yt-dlp.

Searches by ISRC (returns Topic channels = official label audio first).
Downloads best audio as M4A.
Adaptive throttle: detects YouTube rate limiting and backs off automatically.
Throttle state is shared across processes so the widget's detached workers
don't each restart from a zero backoff.
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

# Ask for several results: the first Topic hit is usually right, but when it
# 403s or is region-blocked the caller needs real alternatives to fall back on.
_SEARCH_MAX_RESULTS = 5

_MIN_SEARCH_INTERVAL = 12.0  # seconds between searches (~5/min)
_SEARCH_JITTER = 3.0  # ±3s on interval → 9-15s range

_BACKOFF_BASE = 30  # starting backoff seconds
_BACKOFF_MAX = 1800  # cap at 30 minutes
_JITTER_FACTOR = 0.25  # ±25% jitter on backoff

_BACKOFF_POLL_INTERVAL = 1.0  # cancellation granularity while backing off

_COOKIES_NEEDED_PATTERNS = [
    "sign in to confirm",
    "confirm you're not a bot",
]

_RATE_LIMIT_PATTERNS = [
    "http error 429",
    "too many requests",
]

# YouTube refusing to serve the audio stream to this yt-dlp build. Almost
# always means yt-dlp is behind YouTube's current signature scheme.
_BLOCKED_PATTERNS = [
    "http error 403",
    "forbidden",
    "unable to download video data",
]

# The video itself is gone or geo-restricted — retrying the same URL or
# updating yt-dlp changes nothing, the caller must try another candidate.
_UNAVAILABLE_PATTERNS = [
    "video unavailable",
    "private video",
    "removed by the uploader",
    "not available in your country",
    "this video is unavailable",
]

# macOS TCC blocks access to the cookie file unless the terminal has Full Disk
# Access. yt-dlp surfaces this as `Operation not permitted: …Cookies.binarycookies`.
_TCC_BLOCKED_PATTERNS = [
    "operation not permitted",
    "cookies.binarycookies",
]

_SAFARI_COOKIES_PATH = os.path.expanduser("~/Library/Cookies/Cookies.binarycookies")

# Failure codes handed to callers (and, after translation, to the UI).
ERROR_NOT_FOUND = "youtube_not_found"
ERROR_BLOCKED = "youtube_blocked"
ERROR_UNAVAILABLE = "youtube_unavailable"
ERROR_RATE_LIMITED = "youtube_rate_limited"
ERROR_COOKIES = "youtube_cookies_needed"
ERROR_TIMEOUT = "youtube_timeout"
ERROR_OTHER = "youtube_error"


class DownloadError(RuntimeError):
    """A yt-dlp download failure carrying a machine-readable ``code``.

    Subclasses ``RuntimeError`` so existing ``except RuntimeError`` handlers
    keep working; new callers read ``.code`` to decide whether retrying the
    same URL is pointless (blocked, unavailable) or worth it (timeout).
    """

    def __init__(self, message: str, code: str = ERROR_OTHER) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _SearchOutcome:
    """Result of a single yt-dlp search invocation."""

    candidates: list[dict] = field(default_factory=list)
    is_rate_limited: bool = False
    needs_cookies: bool = False
    # macOS denied access to the Safari cookie jar and cookies were switched
    # off as a result. The next attempt runs without them and usually works,
    # so this must not be punished with an exponential backoff.
    cookies_disabled: bool = False
    error: str = ""
    returncode: int = 0


# ── Rate limit state (module-level, thread-safe) ────────────────────────────

_lock = threading.Lock()
_consecutive_fails: int = 0
_last_search_ts: float = 0.0
_rate_limit_callback: Callable[[int, str], None] | None = None
_cookies_callback: Callable[[], bool] | None = None
_cancel_check: Callable[[], bool] | None = None
_state_path: str = ""
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


def set_cancel_check(callback: Callable[[], bool] | None) -> None:
    """Register a predicate telling whether the current batch was cancelled.

    Polled while backing off so a 30-minute wait can be interrupted in about
    a second instead of blocking the whole run. Pass None to unregister.
    """
    global _cancel_check  # noqa: PLW0603
    _cancel_check = callback


def set_state_path(path: str) -> None:
    """Point the shared throttle state at ``path`` (JSON, best-effort).

    Each widget import runs in a fresh process. Without a shared file the
    adaptive backoff restarts from zero on every click, which is exactly the
    pattern that gets the machine rate-limited by YouTube.
    """
    global _state_path  # noqa: PLW0603
    _state_path = path
    _load_state()


def reset_throttle() -> None:
    """Reset throttle state (e.g. at start of a new batch)."""
    global _consecutive_fails, _last_search_ts  # noqa: PLW0603
    with _lock:
        _consecutive_fails = 0
        _last_search_ts = 0.0
    _save_state()


def classify_error(stderr: str) -> str:
    """Map a yt-dlp stderr blob to one of the ``ERROR_*`` codes."""
    lower = (stderr or "").lower()
    if _detect_cookies_needed(lower):
        return ERROR_COOKIES
    if _detect_rate_limit(lower):
        return ERROR_RATE_LIMITED
    if any(pattern in lower for pattern in _UNAVAILABLE_PATTERNS):
        return ERROR_UNAVAILABLE
    if any(pattern in lower for pattern in _BLOCKED_PATTERNS):
        return ERROR_BLOCKED
    if "timeout" in lower:
        return ERROR_TIMEOUT
    return ERROR_OTHER


def extract_error(stderr: str) -> str:
    """Return the meaningful yt-dlp error line out of a noisy stderr blob.

    yt-dlp prints its "your version is older than 90 days" WARNING before the
    actual failure. Logging the head of stderr therefore captured the warning
    and threw away the cause, which made every field failure undiagnosable.
    """
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    if not lines:
        return ""
    errors = [line for line in lines if line.upper().startswith("ERROR")]
    if errors:
        return errors[-1]
    informative = [line for line in lines if not line.upper().startswith("WARNING")]
    return informative[-1] if informative else lines[-1]


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
    """
    candidates, _ = search_by_isrc_detailed(isrc)
    return candidates


def search_by_isrc_detailed(isrc: str) -> tuple[list[dict], str]:
    """Search YouTube by ISRC. Returns ``(candidates, failure_code)``.

    ``failure_code`` is "" on success **and** on a clean "no such track"
    result — an empty candidate list with an empty code means the ISRC simply
    isn't on YouTube. Otherwise it is one of the ``ERROR_*`` constants, so the
    caller can tell "not there" from "blocked", "rate-limited" or
    "needs cookies" instead of collapsing everything into one opaque failure.
    """
    if not isrc:
        return [], ""

    _throttle_wait()
    outcome = _do_search(isrc)

    # Clean results → success, reset fail counter
    if outcome.candidates:
        _record_success()
        return outcome.candidates, ""

    # Clean search, 0 results, no error → track genuinely absent, no backoff
    if (
        not outcome.is_rate_limited
        and not outcome.needs_cookies
        and outcome.returncode == 0
        and not outcome.error
    ):
        log_event("youtube_search", isrc=isrc, results=0, duration_ms=0)
        return [], ""

    # Cookies just got switched off because macOS denied the cookie jar. The
    # failure says nothing about YouTube — retry straight away rather than
    # sitting out a 30s backoff for a local permission problem.
    if outcome.cookies_disabled:
        retry = _do_search(isrc)
        if retry.candidates:
            _record_success()
            return retry.candidates, ""
        outcome = retry

    # Cookies needed (age-gate, bot-confirm) → prompt user once per session
    if outcome.needs_cookies:
        return _handle_cookies_needed(isrc)

    # A clean second attempt with 0 results means the track isn't there.
    if not outcome.is_rate_limited and outcome.returncode == 0 and not outcome.error:
        log_event("youtube_search", isrc=isrc, results=0, duration_ms=0)
        return [], ""

    # Rate-limit or yt-dlp error → exponential backoff
    backoff = _record_fail()
    reason = outcome.error[:200] or "YouTube error"
    code = classify_error(outcome.error) if outcome.error else ERROR_OTHER

    _notify_rate_limit(backoff, reason)
    if not _sleep_backoff(backoff):
        log_event("youtube_search", isrc=isrc, results=0, cancelled=True, duration_ms=0)
        return [], code

    # Retry once after backoff
    retry = _do_search(isrc)
    if retry.candidates:
        _record_success()
        return retry.candidates, ""
    if retry.needs_cookies:
        return _handle_cookies_needed(isrc)

    log_event("youtube_search", isrc=isrc, results=0, retried=True, duration_ms=0)
    return [], classify_error(retry.error) if retry.error else code


def download_track(url: str, output_dir: str) -> tuple[str, int | None]:
    """Download a YouTube audio as M4A. Returns (filepath, duration).

    Raises RuntimeError on failure.
    """
    os.makedirs(output_dir, exist_ok=True)
    output_template = os.path.join(output_dir, "%(id)s.%(ext)s")
    t0 = time.monotonic()

    cmd = [_executable()]
    if _use_cookies:
        cmd.extend(["--cookies-from-browser", "safari"])
    cmd.extend(
        [
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
        ]
    )

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
        log_event(
            "youtube_download_failed",
            url=url,
            code=ERROR_TIMEOUT,
            reason="timeout",
            duration_ms=duration_ms,
        )
        raise DownloadError(f"yt-dlp timeout after {_DOWNLOAD_TIMEOUT}s", ERROR_TIMEOUT) from None

    if result.returncode != 0:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _cleanup_partial(output_dir)
        stderr = result.stderr.strip()
        if _detect_tcc_blocked(stderr) and _use_cookies:
            _auto_disable_cookies()
        # Log the actual ERROR line, not the head of stderr — yt-dlp puts its
        # version WARNING first and it used to eat the whole 200-char budget.
        detail = extract_error(stderr)
        code = classify_error(stderr)
        log_event(
            "youtube_download_failed",
            url=url,
            code=code,
            reason=detail[:200],
            duration_ms=duration_ms,
        )
        raise DownloadError(f"yt-dlp error: {detail}", code) from None

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
    log_event(
        "youtube_download_failed",
        url=url,
        code=ERROR_OTHER,
        reason="file_not_found",
        duration_ms=duration_ms,
    )
    raise DownloadError("Audio file not found after download", ERROR_OTHER)


# ── Private Functions ────────────────────────────────────────────────────────


def _executable() -> str:
    """Return the yt-dlp to run — the newest one, not just the first in PATH."""
    from music_manager.core.checks import yt_dlp_path  # noqa: PLC0415

    return yt_dlp_path() or "yt-dlp"


def _do_search(isrc: str) -> _SearchOutcome:
    """Execute a single yt-dlp search. Returns outcome with error context."""
    t0 = time.monotonic()
    cmd = [_executable()]
    if _use_cookies:
        cmd.extend(["--cookies-from-browser", "safari"])
    cmd.extend(
        [
            "--dump-json",
            "--skip-download",
            "--no-playlist",
            "--quiet",
            f"ytsearch{_SEARCH_MAX_RESULTS}:{isrc}",
        ]
    )
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
        tcc_blocked = _detect_tcc_blocked(stderr)
        cookies_disabled = False
        if tcc_blocked and _use_cookies:
            _auto_disable_cookies()
            cookies_disabled = True
        cookies_needed = not tcc_blocked and _detect_cookies_needed(stderr)
        rate_limited = not tcc_blocked and not cookies_needed and _detect_rate_limit(stderr)
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
            cookies_disabled=cookies_disabled,
            error=extract_error(stderr)[:200],
            returncode=result.returncode,
        )

    # returncode == 0 → parse candidates
    candidates = []
    seen_ids: set[str] = set()
    for line in result.stdout.strip().splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        video_id = str(data.get("id") or "")
        # Some extractors omit webpage_url; the watch URL is derivable from
        # the id and a candidate without a usable URL can't be downloaded.
        url = str(data.get("webpage_url") or "")
        if not url and video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"
        if not url or (video_id and video_id in seen_ids):
            continue
        if video_id:
            seen_ids.add(video_id)
        candidates.append(
            {
                "id": video_id,
                "title": data.get("title", ""),
                "url": url,
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
    """Enforce minimum interval between searches with jitter (9-15s).

    Uses wall-clock time so the interval is honoured across processes: every
    widget import is a brand-new worker, and a per-process monotonic clock let
    them all fire immediately.
    """
    global _last_search_ts  # noqa: PLW0603
    jittered = _MIN_SEARCH_INTERVAL + random.uniform(-_SEARCH_JITTER, _SEARCH_JITTER)
    with _lock:
        now = time.time()
        elapsed = now - _last_search_ts
        # A timestamp in the future (another worker reserved a slot) or a
        # backwards clock jump must never produce an unbounded sleep.
        wait = min(max(0.0, jittered - elapsed), _MIN_SEARCH_INTERVAL + _SEARCH_JITTER)
        if _last_search_ts <= 0:
            wait = 0.0
        _last_search_ts = now + wait
    _save_state()

    if wait > 0:
        time.sleep(wait)


def _record_success() -> None:
    """Reset consecutive fail counter on success."""
    global _consecutive_fails  # noqa: PLW0603
    with _lock:
        _consecutive_fails = 0
    _save_state()


def _record_fail() -> int:
    """Increment fail counter. Returns exponential backoff seconds with jitter."""
    global _consecutive_fails  # noqa: PLW0603
    with _lock:
        _consecutive_fails += 1
        fails = _consecutive_fails
    _save_state()

    backoff = _compute_backoff(fails)
    log_event("youtube_rate_limit", consecutive_fails=fails, backoff_seconds=backoff)
    return backoff


def _load_state() -> None:
    """Load the shared throttle state from disk (best-effort, never raises)."""
    global _consecutive_fails, _last_search_ts  # noqa: PLW0603
    if not _state_path:
        return
    try:
        with open(_state_path, encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    with _lock:
        try:
            _consecutive_fails = max(0, int(data.get("consecutive_fails", 0)))
            _last_search_ts = float(data.get("last_search_ts", 0.0))
        except (TypeError, ValueError):
            _consecutive_fails = 0
            _last_search_ts = 0.0


def _save_state() -> None:
    """Persist the shared throttle state (best-effort, never raises)."""
    if not _state_path:
        return
    with _lock:
        payload = {"consecutive_fails": _consecutive_fails, "last_search_ts": _last_search_ts}
    try:
        os.makedirs(os.path.dirname(_state_path), exist_ok=True)
        tmp = f"{_state_path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(payload, file)
        os.replace(tmp, _state_path)
    except OSError:
        pass


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


def _detect_tcc_blocked(stderr: str) -> bool:
    """Check if stderr indicates macOS TCC blocked the cookies file."""
    lower = stderr.lower()
    return all(pattern in lower for pattern in _TCC_BLOCKED_PATTERNS)


def _auto_disable_cookies() -> None:
    """Persist youtube_cookies=False and stop using cookies for this session.

    Triggered when macOS TCC blocks the Safari cookie file. Re-enabling later
    requires either granting Full Disk Access or accepting the next age-gate
    prompt explicitly.
    """
    global _use_cookies, _cookies_decided  # noqa: PLW0603

    _use_cookies = False
    _cookies_decided = True  # avoid re-prompting in the same session

    from music_manager.core.config import save_config  # noqa: PLC0415

    save_config({"youtube_cookies": False})
    log_event("youtube_cookies_auto_disabled", reason="tcc_blocked")


def _handle_cookies_needed(isrc: str) -> tuple[list[dict], str]:
    """Handle a search that needs cookies. Prompts user once per session."""
    global _use_cookies, _cookies_decided  # noqa: PLW0603

    # Already declined this session → skip immediately
    if _cookies_decided and not _use_cookies:
        log_event("youtube_search", isrc=isrc, results=0, duration_ms=0, reason="cookies_declined")
        return [], ERROR_COOKIES

    # Cookies were active but still blocked → expired, reset config
    if _use_cookies:
        _use_cookies = False
        _cookies_decided = False
        from music_manager.core.config import save_config  # noqa: PLC0415

        save_config({"youtube_cookies": False})

    # Ask user via UI callback
    cb = _cookies_callback
    if not cb:
        log_event("youtube_search", isrc=isrc, results=0, duration_ms=0, reason="age_restricted")
        return [], ERROR_COOKIES

    activated = cb()  # blocks until UI responds
    _cookies_decided = True
    _use_cookies = activated

    if not activated:
        log_event("youtube_search", isrc=isrc, results=0, duration_ms=0, reason="cookies_declined")
        return [], ERROR_COOKIES

    # Persist for future sessions
    from music_manager.core.config import save_config  # noqa: PLC0415

    save_config({"youtube_cookies": True})

    # Retry search with cookies now active
    retry = _do_search(isrc)
    if retry.candidates:
        _record_success()
        return retry.candidates, ""

    # Cookies didn't help → disable so we don't re-prompt every track
    _use_cookies = False

    log_event("youtube_search", isrc=isrc, results=0, duration_ms=0, reason="cookies_failed")
    return [], ERROR_COOKIES


def _notify_rate_limit(seconds: int, reason: str = "") -> None:
    """Notify UI callback about rate limit wait."""
    cb = _rate_limit_callback
    if cb:
        try:
            cb(seconds, reason)
        except Exception:  # noqa: BLE001
            pass


def _sleep_backoff(seconds: int) -> bool:
    """Sleep for the backoff period. Returns False if cancelled mid-wait.

    Polled in short slices: a 30-minute backoff used to swallow the cancel
    flag entirely, so "Annuler" in the widget did nothing for half an hour.
    """
    check = _cancel_check
    if check is None:
        time.sleep(seconds)
        return True

    deadline = time.monotonic() + seconds
    while True:
        try:
            if check():
                return False
        except Exception:  # noqa: BLE001
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(_BACKOFF_POLL_INTERVAL, remaining))


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
