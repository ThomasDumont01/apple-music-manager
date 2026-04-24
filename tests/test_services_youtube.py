"""Tests for services/youtube.py — search, download, parse, cleanup."""

import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from music_manager.services.youtube import (
    _cleanup_partial,
    _find_latest_m4a,
    _parse_output,
    download_track,
    search_by_isrc,
)

_PATCH = "music_manager.services.youtube"

# ── search_by_isrc ────────────────────────────────────────────────────────


def test_search_empty_isrc() -> None:
    """Empty ISRC returns empty list without calling yt-dlp."""
    assert search_by_isrc("") == []


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_search_parses_json_lines(mock_run, mock_log) -> None:
    """Multi-line JSON output parsed into candidate dicts."""
    line1 = json.dumps(
        {
            "id": "vid1",
            "title": "Song A",
            "webpage_url": "https://yt/1",
            "duration": 200,
            "channel": "Artist - Topic",
        }
    )
    line2 = json.dumps(
        {
            "id": "vid2",
            "title": "Song B",
            "webpage_url": "https://yt/2",
            "duration": 180,
            "channel": "SomeUser",
        }
    )
    mock_run.return_value = MagicMock(stdout=f"{line1}\n{line2}\n", returncode=0)

    results = search_by_isrc("ISRC123")

    assert len(results) == 2
    assert results[0]["channel"] == "Artist - Topic"
    assert results[1]["channel"] == "SomeUser"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_search_topic_channel_priority(mock_run, mock_log) -> None:
    """Topic channels sorted before non-Topic."""
    line1 = json.dumps(
        {
            "id": "v1",
            "title": "X",
            "webpage_url": "u1",
            "duration": 100,
            "channel": "Random Channel",
        }
    )
    line2 = json.dumps(
        {
            "id": "v2",
            "title": "X",
            "webpage_url": "u2",
            "duration": 100,
            "channel": "Artist - Topic",
        }
    )
    mock_run.return_value = MagicMock(stdout=f"{line1}\n{line2}\n", returncode=0)

    results = search_by_isrc("ISRC1")
    assert results[0]["id"] == "v2"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_search_invalid_json_skipped(mock_run, mock_log) -> None:
    """Invalid JSON lines silently skipped."""
    valid = json.dumps(
        {
            "id": "v1",
            "title": "X",
            "webpage_url": "u",
            "duration": 100,
            "channel": "Ch",
        }
    )
    mock_run.return_value = MagicMock(stdout=f"not json\n{valid}\n", returncode=0)

    results = search_by_isrc("ISRC1")
    assert len(results) == 1


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_search_empty_stdout(mock_run, mock_log) -> None:
    """Empty stdout → empty list."""
    mock_run.return_value = MagicMock(stdout="", returncode=0)
    assert search_by_isrc("ISRC1") == []


@patch(f"{_PATCH}.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=30))
def test_search_timeout_returns_empty(mock_run) -> None:
    """Subprocess timeout → empty list."""
    assert search_by_isrc("ISRC1") == []


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.subprocess.run")
def test_search_missing_fields_default(mock_run, mock_log) -> None:
    """Missing JSON fields use defaults."""
    mock_run.return_value = MagicMock(stdout=json.dumps({"id": "v1"}) + "\n", returncode=0)

    results = search_by_isrc("ISRC1")
    assert results[0]["title"] == ""
    assert results[0]["url"] == ""
    assert results[0]["duration"] == 0
    assert results[0]["channel"] == ""


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
def test_download_success(mock_run, tmp_path: Path) -> None:
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
def test_download_nonzero_returncode_raises(mock_run, tmp_path: Path) -> None:
    """Non-zero return code → RuntimeError."""
    mock_run.return_value = MagicMock(returncode=1, stderr="error msg", stdout="")
    with pytest.raises(RuntimeError, match="yt-dlp error"):
        download_track("https://yt/vid1", str(tmp_path))


@patch(
    f"{_PATCH}.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=120)
)
def test_download_timeout_raises(mock_run, tmp_path: Path) -> None:
    """Subprocess timeout → RuntimeError."""
    with pytest.raises(RuntimeError, match="timeout"):
        download_track("https://yt/vid1", str(tmp_path))


@patch(f"{_PATCH}.subprocess.run")
def test_download_fallback_to_latest_m4a(mock_run, tmp_path: Path) -> None:
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
def test_download_no_file_found_raises(mock_run, tmp_path: Path) -> None:
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
