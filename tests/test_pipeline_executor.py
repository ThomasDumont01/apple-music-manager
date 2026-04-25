"""Tests for pipeline/executor.py — parallel import pipeline."""

from pathlib import Path
from unittest.mock import patch

import pytest

from music_manager.core.config import Paths
from music_manager.core.models import Track
from music_manager.pipeline.executor import (
    _download_with_retry,
    run_import_pipeline,
)
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.pipeline.executor"


def _make_track(isrc: str = "ISRC1", title: str = "Song", artist: str = "Artist") -> Track:
    return Track(
        title=title,
        artist=artist,
        album="Album",
        isrc=isrc,
        deezer_id=100,
        album_id=1,
        duration=200,
    )


@pytest.fixture()
def _env(tmp_path: Path):
    """Create paths, tracks_store, albums_store for tests."""
    paths = Paths(str(tmp_path / "data"))
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    return paths, tracks, albums


# ── run_import_pipeline ──────────────────────────────────────────────────


def test_empty_input(_env) -> None:
    """Empty track list returns empty result."""
    paths, tracks, albums = _env
    result = run_import_pipeline([], paths, tracks, albums)
    assert result.imported == 0
    assert result.pending == []


@patch(f"{_PATCH}.log_event")
@patch("music_manager.services.apple.import_file", return_value="APPLE1")
@patch("music_manager.services.tagger.tag_audio_file", return_value=True)
@patch("music_manager.services.tagger.strip_youtube_tags")
@patch("music_manager.services.youtube.download_track", return_value=("/tmp/audio.m4a", 200))
@patch("music_manager.services.youtube.search_by_isrc")
@patch("music_manager.pipeline.importer.download_cover", return_value="/tmp/cover.jpg")
@patch(f"{_PATCH}._cleanup_file")
@patch("music_manager.services.youtube._MIN_SEARCH_INTERVAL", 0)
def test_single_track_success(
    mock_cleanup, mock_cover, mock_search, mock_dl, mock_strip,
    mock_tag, mock_import, mock_log, _env,
) -> None:
    """Single track flows through all 3 stages successfully."""
    paths, tracks, albums = _env
    mock_search.return_value = [
        {"url": "https://yt/v1", "id": "v1", "title": "Song",
         "channel": "Topic", "duration": 200},
    ]

    track = _make_track()
    result = run_import_pipeline([(track, "", "", "")], paths, tracks, albums)

    assert result.imported == 1
    assert result.pending == []
    mock_search.assert_called_once_with("ISRC1")
    mock_dl.assert_called_once()
    mock_tag.assert_called_once()
    mock_import.assert_called_once()


@patch(f"{_PATCH}.log_event")
@patch("music_manager.services.youtube.search_by_isrc", return_value=[])
@patch("music_manager.pipeline.importer.download_cover", return_value="")
@patch("music_manager.services.youtube._MIN_SEARCH_INTERVAL", 0)
def test_youtube_search_fails_creates_pending(mock_cover, mock_search, mock_log, _env) -> None:
    """No YouTube results → PendingTrack with reason=youtube_failed."""
    paths, tracks, albums = _env
    track = _make_track()
    result = run_import_pipeline([(track, "T", "A", "Al")], paths, tracks, albums)

    assert result.imported == 0
    assert len(result.pending) == 1
    assert result.pending[0].reason == "youtube_failed"
    assert result.pending[0].csv_title == "T"


@patch(f"{_PATCH}.log_event")
@patch("music_manager.services.youtube.download_track", side_effect=RuntimeError("fail"))
@patch("music_manager.services.youtube.search_by_isrc")
@patch("music_manager.pipeline.importer.download_cover", return_value="")
@patch("music_manager.services.youtube._MIN_SEARCH_INTERVAL", 0)
@patch(f"{_PATCH}._RETRY_DELAYS", (0, 0))
def test_download_fails_creates_pending(mock_cover, mock_search, mock_dl, mock_log, _env) -> None:
    """Download failure after retries → PendingTrack."""
    paths, tracks, albums = _env
    mock_search.return_value = [{"url": "https://yt/v1"}]
    track = _make_track()
    result = run_import_pipeline([(track, "", "", "")], paths, tracks, albums)

    assert result.imported == 0
    assert len(result.pending) == 1
    assert result.pending[0].reason == "youtube_failed"


@patch(f"{_PATCH}.log_event")
@patch("music_manager.services.apple.import_file", side_effect=RuntimeError("apple fail"))
@patch("music_manager.services.tagger.tag_audio_file", return_value=True)
@patch("music_manager.services.tagger.strip_youtube_tags")
@patch("music_manager.services.youtube.download_track", return_value=("/tmp/a.m4a", 200))
@patch("music_manager.services.youtube.search_by_isrc")
@patch("music_manager.pipeline.importer.download_cover", return_value="")
@patch(f"{_PATCH}._cleanup_file")
@patch("music_manager.services.youtube._MIN_SEARCH_INTERVAL", 0)
def test_apple_import_fails_creates_pending(
    mock_cleanup, mock_cover, mock_search, mock_dl, mock_strip,
    mock_tag, mock_import, mock_log, _env,
) -> None:
    """Apple Music import failure → PendingTrack."""
    paths, tracks, albums = _env
    mock_search.return_value = [{"url": "https://yt/v1"}]
    track = _make_track()
    result = run_import_pipeline([(track, "", "", "")], paths, tracks, albums)

    assert result.imported == 0
    assert len(result.pending) == 1
    assert result.pending[0].reason == "apple_import_failed"


@patch(f"{_PATCH}.log_event")
@patch("music_manager.services.youtube.search_by_isrc", return_value=[])
@patch("music_manager.pipeline.importer.download_cover", return_value="")
@patch("music_manager.services.youtube._MIN_SEARCH_INTERVAL", 0)
def test_progress_callback(mock_cover, mock_search, mock_log, _env) -> None:
    """Progress callback called for each completed track."""
    paths, tracks, albums = _env
    progress_calls = []

    def on_progress(done: int, total: int) -> None:
        progress_calls.append((done, total))

    items = [(_make_track(f"ISRC{i}"), "", "", "") for i in range(3)]
    run_import_pipeline(items, paths, tracks, albums, on_progress=on_progress)

    assert len(progress_calls) == 3
    assert progress_calls[-1] == (3, 3)


@patch(f"{_PATCH}.log_event")
@patch("music_manager.services.youtube.search_by_isrc", return_value=[])
@patch("music_manager.pipeline.importer.download_cover", return_value="")
@patch("music_manager.services.youtube._MIN_SEARCH_INTERVAL", 0)
def test_cancellation_stops_pipeline(mock_cover, mock_search, mock_log, _env) -> None:
    """should_cancel=True stops processing remaining tracks."""
    paths, tracks, albums = _env
    call_count = [0]

    def mock_search_counting(isrc):
        call_count[0] += 1
        return []

    mock_search.side_effect = mock_search_counting
    cancel_after = [1]

    def should_cancel():
        return call_count[0] >= cancel_after[0]

    items = [(_make_track(f"ISRC{i}"), "", "", "") for i in range(10)]
    run_import_pipeline(
        items, paths, tracks, albums, should_cancel=should_cancel,
    )

    # Should not have processed all 10
    assert call_count[0] < 10


@patch(f"{_PATCH}.log_event")
@patch("music_manager.services.youtube.download_track", return_value=("/tmp/a.m4a", 300))
@patch("music_manager.services.youtube.search_by_isrc")
@patch("music_manager.pipeline.importer.download_cover", return_value="")
@patch("music_manager.services.tagger.strip_youtube_tags")
@patch(f"{_PATCH}._cleanup_file")
@patch("music_manager.services.youtube._MIN_SEARCH_INTERVAL", 0)
def test_duration_suspect_creates_pending(
    mock_cleanup, mock_strip, mock_cover, mock_search, mock_dl, mock_log, _env,
) -> None:
    """Duration ratio outside 0.93-1.07 → pending with reason=duration_suspect."""
    paths, tracks, albums = _env
    mock_search.return_value = [{"url": "https://yt/v1"}]

    track = _make_track()
    track.duration = 200  # actual=300, ratio=1.5 → suspect
    result = run_import_pipeline([(track, "", "", "")], paths, tracks, albums)

    assert result.imported == 0
    assert len(result.pending) == 1
    assert result.pending[0].reason == "duration_suspect"


# ── _download_with_retry ─────────────────────────────────────────────────


def test_download_retry_succeeds_on_second() -> None:
    """First attempt fails, second succeeds."""
    call_count = [0]

    def download_fn(url, output_dir):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("transient")
        return "/tmp/audio.m4a", 200

    with patch(f"{_PATCH}._RETRY_DELAYS", (0, 0)):
        path, dur = _download_with_retry("url", "/tmp", download_fn)

    assert path == "/tmp/audio.m4a"
    assert call_count[0] == 2


def test_download_retry_exhausted_returns_none() -> None:
    """All attempts fail → (None, None)."""
    def download_fn(url, output_dir):
        raise RuntimeError("permanent")

    with patch(f"{_PATCH}._RETRY_DELAYS", (0, 0)):
        path, dur = _download_with_retry("url", "/tmp", download_fn)

    assert path is None
    assert dur is None


def test_download_retry_first_attempt_ok() -> None:
    """First attempt succeeds → no retries."""
    call_count = [0]

    def download_fn(url, output_dir):
        call_count[0] += 1
        return "/tmp/audio.m4a", 180

    path, dur = _download_with_retry("url", "/tmp", download_fn)

    assert path == "/tmp/audio.m4a"
    assert dur == 180
    assert call_count[0] == 1
