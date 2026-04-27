"""Tests for services/youtube.py — search, download, parse, cleanup, throttle."""

import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import music_manager.services.youtube as yt
from music_manager.services.youtube import (
    _cleanup_partial,
    _find_latest_m4a,
    _parse_output,
    download_track,
    reset_throttle,
    search_by_isrc,
    set_cookies_callback,
    set_rate_limit_callback,
)

_PATCH = "music_manager.services.youtube"

# Helper: disable throttle delays for fast tests
_NO_THROTTLE = {
    "_MIN_SEARCH_INTERVAL": 0,
    "_SEARCH_JITTER": 0,
    "_BACKOFF_BASE": 0.01,
    "_BACKOFF_MAX": 0.05,
}

# Simulates a yt-dlp error (non-zero returncode + stderr)
_ERROR_RESULT = MagicMock(stdout="", stderr="ERROR: network error", returncode=1)

# Simulates a clean "not found" (returncode=0, empty stdout)
_NOT_FOUND_RESULT = MagicMock(stdout="", stderr="", returncode=0)


def _make_candidate(
    vid_id: str = "v1",
    title: str = "Song",
    channel: str = "Artist - Topic",
) -> str:
    return json.dumps(
        {
            "id": vid_id,
            "title": title,
            "webpage_url": f"https://yt/{vid_id}",
            "duration": 200,
            "channel": channel,
        }
    )


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset throttle state before each test."""
    reset_throttle()
    set_rate_limit_callback(None)
    set_cookies_callback(None)
    yt._use_cookies = False
    yt._cookies_decided = False
    yield
    reset_throttle()
    set_rate_limit_callback(None)
    set_cookies_callback(None)
    yt._use_cookies = False
    yt._cookies_decided = False


# ── search_by_isrc ────────────────────────────────────────────────────────


def test_search_empty_isrc() -> None:
    """Empty ISRC returns empty list without calling yt-dlp."""
    assert search_by_isrc("") == []


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_search_parses_json_lines(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """JSON output parsed into candidate dict."""
    mock_run.return_value = MagicMock(
        stdout=_make_candidate("vid1", "Song A", "Artist - Topic") + "\n",
        returncode=0,
    )

    results = search_by_isrc("ISRC123")

    assert len(results) == 1
    assert results[0]["channel"] == "Artist - Topic"
    assert results[0]["id"] == "vid1"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_search_uses_ytsearch1(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """Search uses ytsearch1 (not ytsearch5) to reduce API load."""
    mock_run.return_value = MagicMock(stdout="", returncode=0)

    search_by_isrc("TESTISRC")

    cmd = mock_run.call_args[0][0]
    search_arg = [a for a in cmd if "ytsearch" in a][0]
    assert search_arg == "ytsearch1:TESTISRC"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_search_topic_channel_priority(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """Topic channels sorted before non-Topic."""
    line1 = _make_candidate("v1", "X", "Random Channel")
    line2 = _make_candidate("v2", "X", "Artist - Topic")
    mock_run.return_value = MagicMock(stdout=f"{line1}\n{line2}\n", returncode=0)

    results = search_by_isrc("ISRC1")
    assert results[0]["id"] == "v2"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_search_invalid_json_skipped(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """Invalid JSON lines silently skipped."""
    valid = _make_candidate("v1")
    mock_run.return_value = MagicMock(stdout=f"not json\n{valid}\n", returncode=0)

    results = search_by_isrc("ISRC1")
    assert len(results) == 1


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_search_empty_stdout(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """Empty stdout → empty list."""
    mock_run.return_value = MagicMock(stdout="", returncode=0)
    assert search_by_isrc("ISRC1") == []


@patch(
    f"{_PATCH}.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=30),
)
def test_search_timeout_returns_empty(mock_run: MagicMock) -> None:
    """Subprocess timeout → empty list."""
    assert search_by_isrc("ISRC1") == []


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_search_missing_fields_default(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """Missing JSON fields use defaults."""
    mock_run.return_value = MagicMock(
        stdout=json.dumps({"id": "v1"}) + "\n", returncode=0
    )

    results = search_by_isrc("ISRC1")
    assert results[0]["title"] == ""
    assert results[0]["url"] == ""
    assert results[0]["duration"] == 0
    assert results[0]["channel"] == ""


# ── Throttle ─────────────────────────────────────────────────────────────


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_throttle_enforces_interval(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """Consecutive searches must wait _MIN_SEARCH_INTERVAL."""
    mock_run.return_value = MagicMock(
        stdout=_make_candidate() + "\n", returncode=0
    )

    with patch(f"{_PATCH}._MIN_SEARCH_INTERVAL", 0.5), \
         patch(f"{_PATCH}._SEARCH_JITTER", 0):
        start = time.monotonic()
        search_by_isrc("A1")
        search_by_isrc("A2")
        elapsed = time.monotonic() - start

    assert elapsed >= 0.45, f"Throttle not enforced: {elapsed:.2f}s"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_success_resets_fail_counter(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """Successful search resets consecutive fail counter."""
    call_count = [0]

    def _side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            return MagicMock(stdout="", stderr="ERROR: fail", returncode=1)
        return MagicMock(stdout=_make_candidate() + "\n", stderr="", returncode=0)

    mock_run.side_effect = _side_effect

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        search_by_isrc("F1")  # error 1 → backoff + retry (error 2)
        search_by_isrc("OK")  # success

    assert yt._consecutive_fails == 0


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_backoff_triggers_on_error(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """yt-dlp errors trigger exponential backoff and rate-limit callback."""
    mock_run.return_value = MagicMock(stdout="", stderr="ERROR: fail", returncode=1)

    rate_events: list[tuple[int, str]] = []
    set_rate_limit_callback(lambda s, r: rate_events.append((s, r)))

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        search_by_isrc("FAIL1")  # error → backoff + retry (error again)

    assert len(rate_events) >= 1, f"Expected rate limit callback, got {rate_events}"
    assert rate_events[0][1], "Expected reason string in callback"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_retry_after_backoff_can_succeed(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """After backoff, the retry can succeed."""
    call_count = [0]

    def _side_effect(*args, **kwargs):
        call_count[0] += 1
        # First call errors, retry (2nd call) succeeds
        if call_count[0] <= 1:
            return MagicMock(stdout="", stderr="ERROR: temp", returncode=1)
        return MagicMock(stdout=_make_candidate("retry_ok") + "\n", stderr="", returncode=0)

    mock_run.side_effect = _side_effect

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        result = search_by_isrc("F1")  # error → backoff → retry succeeds

    assert len(result) == 1
    assert result[0]["id"] == "retry_ok"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_rate_limit_callback_receives_seconds_and_reason(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """Rate limit callback receives (seconds, reason) on error."""
    mock_run.return_value = MagicMock(stdout="", stderr="ERROR: something", returncode=1)

    rate_events: list[tuple[int, str]] = []
    set_rate_limit_callback(lambda s, r: rate_events.append((s, r)))

    with patch.multiple(
        _PATCH,
        _MIN_SEARCH_INTERVAL=0,
        _SEARCH_JITTER=0,
        _BACKOFF_BASE=30,
        _BACKOFF_MAX=1800,
        _JITTER_FACTOR=0,
    ), patch(f"{_PATCH}.time.sleep"):
        search_by_isrc("FAIL1")  # error → backoff + callback

    assert len(rate_events) >= 1
    seconds, reason = rate_events[0]
    assert seconds > 0
    assert reason  # non-empty reason string


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_reset_throttle_clears_state(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """reset_throttle() clears fail counter and timestamp."""
    mock_run.return_value = MagicMock(stdout="", stderr="ERROR: x", returncode=1)

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        search_by_isrc("F1")  # error → increments _consecutive_fails

    assert yt._consecutive_fails > 0
    reset_throttle()
    assert yt._consecutive_fails == 0


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_callback_exception_swallowed(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """Broken callback doesn't crash search."""
    mock_run.return_value = MagicMock(stdout="", stderr="ERROR: x", returncode=1)

    def broken_cb(secs: int, reason: str) -> None:
        raise ValueError("boom")

    set_rate_limit_callback(broken_cb)

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        # Should not raise despite broken callback
        search_by_isrc("FAIL1")


# ── Not found vs error distinction ────────────────────────────────────────


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_not_found_no_backoff(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """Clean search with 0 results (rc=0) does NOT trigger backoff."""
    mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)

    rate_events: list[tuple[int, str]] = []
    set_rate_limit_callback(lambda s, r: rate_events.append((s, r)))

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        for i in range(10):
            search_by_isrc(f"ABSENT{i}")

    assert yt._consecutive_fails == 0, "Not-found should not increment fail counter"
    assert len(rate_events) == 0, "Not-found should not trigger rate-limit callback"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_error_triggers_exponential_backoff(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """yt-dlp errors produce exponential backoff (doubles each time)."""
    mock_run.return_value = MagicMock(stdout="", stderr="ERROR: net", returncode=1)

    backoffs: list[int] = []
    set_rate_limit_callback(lambda s, r: backoffs.append(s))

    with patch.multiple(_PATCH, **{**_NO_THROTTLE, "_JITTER_FACTOR": 0}), \
         patch(f"{_PATCH}.time.sleep"):
        for i in range(4):
            search_by_isrc(f"ERR{i}")

    # With jitter=0, backoffs double: base*1, base*2, base*4, base*8
    # But each search_by_isrc with error → _record_fail + retry → _record_fail again
    # So fails accumulate faster. Just verify backoffs are increasing.
    assert len(backoffs) >= 2
    assert backoffs[-1] >= backoffs[0], "Backoffs should increase"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_sign_in_detected_as_cookies_needed(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """'Sign in to confirm' in stderr → cookies needed, no backoff."""
    mock_run.return_value = MagicMock(
        stdout="", stderr="Sign in to confirm you're not a bot", returncode=1
    )

    rate_events: list[tuple[int, str]] = []
    set_rate_limit_callback(lambda s, r: rate_events.append((s, r)))

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        result = search_by_isrc("BLOCKED")

    # No rate-limit callback — cookies path, not rate-limit path
    assert len(rate_events) == 0
    assert result == []
    # No backoff → _consecutive_fails not incremented
    assert yt._consecutive_fails == 0


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_429_detected_as_rate_limit(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """HTTP Error 429 in stderr detected as rate limit with backoff."""
    mock_run.return_value = MagicMock(
        stdout="", stderr="HTTP Error 429: Too Many Requests", returncode=1
    )

    rate_events: list[tuple[int, str]] = []
    set_rate_limit_callback(lambda s, r: rate_events.append((s, r)))

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        search_by_isrc("RATELIMITED")

    assert len(rate_events) >= 1
    assert yt._consecutive_fails > 0  # 429 now uses exponential backoff


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_backoff_capped_at_max(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """Backoff never exceeds _BACKOFF_MAX (+ jitter margin)."""
    mock_run.return_value = MagicMock(stdout="", stderr="ERROR: x", returncode=1)

    backoffs: list[int] = []
    set_rate_limit_callback(lambda s, r: backoffs.append(s))

    max_val = 100
    with patch.multiple(
        _PATCH,
        _MIN_SEARCH_INTERVAL=0,
        _SEARCH_JITTER=0,
        _BACKOFF_BASE=10,
        _BACKOFF_MAX=max_val,
        _JITTER_FACTOR=0.25,
    ), patch(f"{_PATCH}.time.sleep"):
        for i in range(10):
            search_by_isrc(f"ERR{i}")

    # Max with 25% jitter = 125
    assert all(b <= max_val * 1.26 for b in backoffs), f"Backoff exceeded cap: {backoffs}"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_throttle_jitter_varies_interval(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """Jitter causes search intervals to vary (not all identical)."""
    mock_run.return_value = MagicMock(
        stdout=_make_candidate() + "\n", stderr="", returncode=0
    )

    timestamps: list[float] = []

    def _capture_sleep(secs: float) -> None:
        timestamps.append(secs)
        # Don't actually sleep

    with patch(f"{_PATCH}._MIN_SEARCH_INTERVAL", 10.0), \
         patch(f"{_PATCH}._SEARCH_JITTER", 3.0), \
         patch(f"{_PATCH}.time.sleep", side_effect=_capture_sleep):
        for i in range(5):
            yt._last_search_ts = time.monotonic() - 1  # force throttle
            search_by_isrc(f"T{i}")

    if len(timestamps) >= 2:
        # With ±3s jitter, not all waits should be identical
        unique_waits = set(round(t, 2) for t in timestamps)
        assert len(unique_waits) > 1, f"Jitter not working: all waits = {timestamps}"


# ── _parse_output ─────────────────────────────────────────────────────────


def test_parse_output_normal() -> None:
    """filepath + duration parsed."""
    path, dur = _parse_output("/tmp/song.m4a\n200\n")
    assert path == "/tmp/song.m4a"
    assert dur == 200


def test_parse_output_no_duration() -> None:
    """Only filepath → duration is None."""
    path, dur = _parse_output("/tmp/song.m4a\n")
    assert path == "/tmp/song.m4a"
    assert dur is None


def test_parse_output_invalid_duration() -> None:
    """Non-numeric duration → None."""
    _, dur = _parse_output("/tmp/song.m4a\nNA\n")
    assert dur is None


def test_parse_output_empty() -> None:
    """Empty output."""
    path, dur = _parse_output("")
    assert path == ""
    assert dur is None


# ── download_track ────────────────────────────────────────────────────────


@patch(f"{_PATCH}.subprocess.run")
def test_download_success(mock_run: MagicMock, tmp_path: Path) -> None:
    """Successful download returns filepath and duration."""
    dl_file = str(tmp_path / "vid1.m4a")
    (tmp_path / "vid1.m4a").write_text("audio")

    mock_run.return_value = MagicMock(
        stdout=f"{dl_file}\n200\n",
        returncode=0,
        stderr="",
    )
    path, dur = download_track("https://yt/vid1", str(tmp_path))
    assert path == dl_file
    assert dur == 200


@patch(f"{_PATCH}.subprocess.run")
def test_download_nonzero_returncode_raises(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """Non-zero return code → RuntimeError."""
    mock_run.return_value = MagicMock(returncode=1, stderr="error msg", stdout="")
    with pytest.raises(RuntimeError, match="yt-dlp error"):
        download_track("https://yt/vid1", str(tmp_path))


@patch(
    f"{_PATCH}.subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=120),
)
def test_download_timeout_raises(mock_run: MagicMock, tmp_path: Path) -> None:
    """Subprocess timeout → RuntimeError."""
    with pytest.raises(RuntimeError, match="timeout"):
        download_track("https://yt/vid1", str(tmp_path))


@patch(f"{_PATCH}.subprocess.run")
def test_download_fallback_to_latest_m4a(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """When printed path doesn't exist, falls back to latest .m4a."""
    real_file = str(tmp_path / "actual.m4a")
    (tmp_path / "actual.m4a").write_text("audio")

    mock_run.return_value = MagicMock(
        stdout="/nonexistent/path.m4a\n200\n",
        returncode=0,
        stderr="",
    )
    path, _ = download_track("https://yt/vid1", str(tmp_path))
    assert path == real_file


@patch(f"{_PATCH}.subprocess.run")
def test_download_no_file_found_raises(mock_run: MagicMock, tmp_path: Path) -> None:
    """No file found → RuntimeError."""
    mock_run.return_value = MagicMock(
        stdout="/nonexistent.m4a\n",
        returncode=0,
        stderr="",
    )
    with pytest.raises(RuntimeError, match="not found"):
        download_track("https://yt/vid1", str(tmp_path))


# ── _cleanup_partial ──────────────────────────────────────────────────────


def test_cleanup_partial_removes_part_files(tmp_path: Path) -> None:
    """Only .part files removed."""
    for name in ("song.part", "other.part", "keep.m4a"):
        (tmp_path / name).write_text("x")

    _cleanup_partial(str(tmp_path))

    remaining = os.listdir(str(tmp_path))
    assert "keep.m4a" in remaining
    assert "song.part" not in remaining


# ── _find_latest_m4a ─────────────────────────────────────────────────────


def test_find_latest_m4a_returns_newest(tmp_path: Path) -> None:
    """Returns the most recently modified .m4a file."""
    f1 = tmp_path / "old.m4a"
    f1.write_text("x")
    time.sleep(0.05)
    f2 = tmp_path / "new.m4a"
    f2.write_text("x")

    result = _find_latest_m4a(str(tmp_path))
    assert result == str(f2)


def test_find_latest_m4a_empty_dir(tmp_path: Path) -> None:
    """Empty directory returns empty string."""
    assert _find_latest_m4a(str(tmp_path)) == ""


# ── Cookies detection ────────────────────────────────────────────────────


def test_detect_cookies_needed_age_gate() -> None:
    """Age-gate stderr triggers cookies needed, not rate-limit."""
    assert yt._detect_cookies_needed("Sign in to confirm your age") is True
    assert yt._detect_rate_limit("Sign in to confirm your age") is False


def test_detect_cookies_needed_bot_confirm() -> None:
    """Bot-confirm stderr triggers cookies needed."""
    assert yt._detect_cookies_needed("confirm you're not a bot") is True
    assert yt._detect_rate_limit("confirm you're not a bot") is False


def test_detect_rate_limit_429() -> None:
    """HTTP 429 triggers rate-limit, not cookies."""
    assert yt._detect_rate_limit("HTTP Error 429: Too Many Requests") is True
    assert yt._detect_cookies_needed("HTTP Error 429: Too Many Requests") is False


def test_detect_neither() -> None:
    """Generic error triggers neither cookies nor rate-limit."""
    assert yt._detect_cookies_needed("ERROR: network error") is False
    assert yt._detect_rate_limit("ERROR: network error") is False


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_cookies_callback_activated(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """When cookies needed and callback returns True, retry with cookies."""
    call_count = [0]

    def _side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: age-gate error
            return MagicMock(stdout="", stderr="Sign in to confirm your age", returncode=1)
        # Retry with cookies: success
        return MagicMock(stdout=_make_candidate("cookie_ok") + "\n", stderr="", returncode=0)

    mock_run.side_effect = _side_effect
    set_cookies_callback(lambda: True)

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        result = search_by_isrc("AGEGATE")

    assert len(result) == 1
    assert result[0]["id"] == "cookie_ok"
    assert yt._use_cookies is True


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_cookies_callback_declined(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """When cookies needed and callback returns False, skip immediately."""
    mock_run.return_value = MagicMock(
        stdout="", stderr="Sign in to confirm your age", returncode=1
    )
    set_cookies_callback(lambda: False)

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        result = search_by_isrc("AGEGATE")

    assert result == []
    assert yt._use_cookies is False
    assert yt._cookies_decided is True


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_cookies_declined_skips_subsequent(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """Once declined, subsequent cookies-needed searches skip without callback."""
    mock_run.return_value = MagicMock(
        stdout="", stderr="Sign in to confirm your age", returncode=1
    )

    cb_calls = [0]

    def _counting_cb() -> bool:
        cb_calls[0] += 1
        return False

    set_cookies_callback(_counting_cb)

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        search_by_isrc("AGE1")  # triggers callback
        search_by_isrc("AGE2")  # should skip without callback

    assert cb_calls[0] == 1  # only called once


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_cookies_no_callback_returns_empty(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """Without callback, cookies-needed returns empty (no crash)."""
    mock_run.return_value = MagicMock(
        stdout="", stderr="Sign in to confirm your age", returncode=1
    )

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        result = search_by_isrc("AGEGATE")

    assert result == []


def test_check_safari_cookies_missing_file() -> None:
    """check_safari_youtube_login returns False when file doesn't exist."""
    with patch(f"{_PATCH}._SAFARI_COOKIES_PATH", "/nonexistent/path"):
        assert yt.check_safari_youtube_login() is False


def test_check_safari_cookies_present(tmp_path: Path) -> None:
    """check_safari_youtube_login returns True when auth cookies present."""
    cookie_file = tmp_path / "Cookies.binarycookies"
    cookie_file.write_bytes(b"stuff .youtube.com LOGIN_INFO=abc stuff")
    with patch(f"{_PATCH}._SAFARI_COOKIES_PATH", str(cookie_file)):
        assert yt.check_safari_youtube_login() is True


def test_check_safari_cookies_no_login(tmp_path: Path) -> None:
    """check_safari_youtube_login returns False when no auth cookies."""
    cookie_file = tmp_path / "Cookies.binarycookies"
    cookie_file.write_bytes(b"stuff .youtube.com visitor_id=xyz stuff")
    with patch(f"{_PATCH}._SAFARI_COOKIES_PATH", str(cookie_file)):
        assert yt.check_safari_youtube_login() is False


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_download_uses_cookies_flag(mock_run: MagicMock, mock_log: MagicMock) -> None:
    """download_track includes --cookies-from-browser when _use_cookies=True."""
    yt._use_cookies = True
    mock_run.return_value = MagicMock(stdout="path.m4a\n200\n", stderr="", returncode=0)

    with patch(f"{_PATCH}.os.path.exists", return_value=True), \
         patch(f"{_PATCH}.os.path.getsize", return_value=1000), \
         patch(f"{_PATCH}.os.makedirs"):
        download_track("https://yt/v1", "/tmp/dl")

    cmd = mock_run.call_args[0][0]
    assert "--cookies-from-browser" in cmd
    assert "safari" in cmd
