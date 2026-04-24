"""Tests for pipeline/importer.py — centralized import logic."""

from pathlib import Path
from unittest.mock import patch

from music_manager.core.config import Paths
from music_manager.core.models import Track
from music_manager.pipeline.importer import import_resolved_track
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.pipeline.importer"


def _track(**overrides) -> Track:
    """Create a minimal Track for import."""
    defaults = {
        "isrc": "ISRC123",
        "title": "Song",
        "artist": "Artist",
        "album": "Album",
        "deezer_id": 1,
        "album_id": 1,
        "duration": 200,
        "cover_url": "https://cover.jpg",
    }
    defaults.update(overrides)
    return Track(**defaults)


def _paths(tmp_path: Path) -> Paths:
    return Paths(str(tmp_path / "data"))


# ── Success path ────────────────────────────────────────────────────────────


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}._cleanup")
@patch(f"{_PATCH}.import_file", return_value="APPLE_NEW")
@patch(f"{_PATCH}.tag_audio_file")
@patch(f"{_PATCH}.download_track", return_value=("/tmp/song.m4a", 200))
@patch(f"{_PATCH}.search_by_isrc", return_value=[{"url": "https://yt/video", "topic": True}])
def test_import_success(
    mock_yt_search, mock_dl, mock_tag, mock_apple, mock_clean, mock_log, tmp_path
) -> None:
    """Full pipeline: YouTube → download → tag → Apple import → store update."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    albums.put(1, {"cover_url": "https://cover.jpg"})
    paths = _paths(tmp_path)

    track = _track()
    result = import_resolved_track(track, paths, tracks, albums)

    assert result is None  # success
    mock_yt_search.assert_called_once_with("ISRC123")
    mock_dl.assert_called_once()
    mock_tag.assert_called_once()
    mock_apple.assert_called_once()

    # Track should be in store
    entry = tracks.get_by_apple_id("APPLE_NEW")
    assert entry is not None
    assert entry["status"] == "done"
    assert entry["origin"] == "imported"
    assert entry["isrc"] == "ISRC123"


# ── YouTube failures ────────────────────────────────────────────────────────


@patch(f"{_PATCH}.search_by_isrc", return_value=[])
def test_import_youtube_no_candidates(mock_search, tmp_path) -> None:
    """No YouTube results → PendingTrack with reason youtube_failed."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    result = import_resolved_track(_track(), paths, tracks, albums)

    assert result is not None
    assert result.reason == "youtube_failed"


@patch(f"{_PATCH}.download_track", return_value=(None, None))
@patch(f"{_PATCH}.search_by_isrc", return_value=[{"url": "https://yt/1"}, {"url": "https://yt/2"}])
def test_import_youtube_download_fails(mock_search, mock_dl, tmp_path) -> None:
    """Download fails after retry → PendingTrack with remaining candidates."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    result = import_resolved_track(_track(), paths, tracks, albums)

    assert result is not None
    assert result.reason == "youtube_failed"
    assert len(result.youtube_candidates) == 1  # second candidate preserved


# ── Duration check ──────────────────────────────────────────────────────────


@patch(f"{_PATCH}.download_track", return_value=("/tmp/song.m4a", 300))
@patch(f"{_PATCH}.search_by_isrc", return_value=[{"url": "https://yt/1"}])
def test_import_duration_suspect(mock_search, mock_dl, tmp_path) -> None:
    """Duration ratio outside 0.93-1.07 → PendingTrack duration_suspect."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    track = _track(duration=200)  # expected 200s, got 300s → ratio 1.5
    result = import_resolved_track(track, paths, tracks, albums)

    assert result is not None
    assert result.reason == "duration_suspect"
    assert result.actual_duration == 300
    assert result.dl_path == "/tmp/song.m4a"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}._cleanup")
@patch(f"{_PATCH}.import_file", return_value="APPLE_NEW")
@patch(f"{_PATCH}.tag_audio_file")
@patch(f"{_PATCH}.download_track", return_value=("/tmp/song.m4a", 205))
@patch(f"{_PATCH}.search_by_isrc", return_value=[{"url": "https://yt/1"}])
def test_import_duration_within_tolerance(
    mock_search, mock_dl, mock_tag, mock_apple, mock_clean, mock_log, tmp_path
) -> None:
    """Duration ratio within 0.93-1.07 → import proceeds."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    track = _track(duration=200)  # expected 200s, got 205s → ratio 1.025 ✓
    result = import_resolved_track(track, paths, tracks, albums)

    assert result is None  # success


# ── CSV traceability ────────────────────────────────────────────────────────


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}._cleanup")
@patch(f"{_PATCH}.import_file", return_value="APPLE_NEW")
@patch(f"{_PATCH}.tag_audio_file")
@patch(f"{_PATCH}.download_track", return_value=("/tmp/song.m4a", 200))
@patch(f"{_PATCH}.search_by_isrc", return_value=[{"url": "https://yt/1"}])
def test_import_stores_csv_origin(
    mock_search, mock_dl, mock_tag, mock_apple, mock_clean, mock_log, tmp_path
) -> None:
    """CSV title/artist/album are stored for traceability."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    track = _track()
    import_resolved_track(
        track,
        paths,
        tracks,
        albums,
        csv_title="CSV Song",
        csv_artist="CSV Artist",
        csv_album="CSV Album",
    )

    entry = tracks.get_by_apple_id("APPLE_NEW")
    assert entry is not None
    assert entry["csv_title"] == "CSV Song"
    assert entry["csv_artist"] == "CSV Artist"
    assert entry["csv_album"] == "CSV Album"
