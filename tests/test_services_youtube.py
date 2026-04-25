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
    set_rate_limit_callback,
)

_PATCH = "music_manager.services.youtube"

# Helper: disable throttle delays for fast tests
_NO_THROTTLE = {
    "_MIN_SEARCH_INTERVAL": 0,
    "_BACKOFF_SHORT": 0.01,
    "_BACKOFF_LONG": 0.02,
}


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
    yield
    reset_throttle()
    set_rate_limit_callback(None)


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

    with patch(f"{_PATCH}._MIN_SEARCH_INTERVAL", 0.5):
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
            return MagicMock(stdout="", returncode=0)
        return MagicMock(stdout=_make_candidate() + "\n", returncode=0)

    mock_run.side_effect = _side_effect

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        search_by_isrc("F1")  # fail 1
        search_by_isrc("F2")  # fail 2
        search_by_isrc("OK")  # success

    assert yt._consecutive_fails == 0


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_backoff_triggers_after_threshold(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """After _BACKOFF_THRESHOLD_WARN consecutive fails, backoff is triggered."""
    mock_run.return_value = MagicMock(stdout="", returncode=0)

    rate_events: list[int] = []
    set_rate_limit_callback(rate_events.append)

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        for i in range(4):
            search_by_isrc(f"FAIL{i}")

    # 3rd fail triggers short backoff, 4th triggers again
    assert len(rate_events) >= 1, f"Expected rate limit callback, got {rate_events}"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_retry_after_backoff_can_succeed(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """After backoff, the failed search is retried and can succeed."""
    call_count = [0]

    def _side_effect(*args, **kwargs):
        call_count[0] += 1
        # First 5 calls fail, 6th succeeds (retry after hard backoff)
        if call_count[0] <= 5:
            return MagicMock(stdout="", returncode=0)
        return MagicMock(stdout=_make_candidate("retry_ok") + "\n", returncode=0)

    mock_run.side_effect = _side_effect

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        search_by_isrc("F1")  # fail 1
        search_by_isrc("F2")  # fail 2
        # F3: fail 3 → short backoff → retry (fail 4)
        search_by_isrc("F3")
        # F4: fail 5 → long backoff → retry (success!)
        result = search_by_isrc("F4")

    assert len(result) == 1
    assert result[0]["id"] == "retry_ok"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_rate_limit_callback_receives_seconds(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """Rate limit callback receives correct backoff seconds."""
    mock_run.return_value = MagicMock(stdout="", returncode=0)

    rate_events: list[int] = []
    set_rate_limit_callback(rate_events.append)

    with patch(f"{_PATCH}._MIN_SEARCH_INTERVAL", 0), \
         patch(f"{_PATCH}._BACKOFF_SHORT", 42), \
         patch(f"{_PATCH}._BACKOFF_LONG", 99):
        # Use sleep patch to avoid actual waiting
        with patch(f"{_PATCH}.time.sleep"):
            for i in range(6):
                search_by_isrc(f"FAIL{i}")

    # Short backoff (42) for fails 3-4, long backoff (99) for fails 5+
    assert 42 in rate_events
    assert 99 in rate_events


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_reset_throttle_clears_state(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """reset_throttle() clears fail counter and timestamp."""
    mock_run.return_value = MagicMock(stdout="", returncode=0)

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        search_by_isrc("F1")
        search_by_isrc("F2")

    assert yt._consecutive_fails > 0
    reset_throttle()
    assert yt._consecutive_fails == 0


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_callback_exception_swallowed(
    mock_run: MagicMock, mock_log: MagicMock
) -> None:
    """Broken callback doesn't crash search."""
    mock_run.return_value = MagicMock(stdout="", returncode=0)

    def broken_cb(secs: int) -> None:
        raise ValueError("boom")

    set_rate_limit_callback(broken_cb)

    with patch.multiple(_PATCH, **_NO_THROTTLE):
        # Should not raise despite broken callback
        for i in range(4):
            search_by_isrc(f"FAIL{i}")


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
