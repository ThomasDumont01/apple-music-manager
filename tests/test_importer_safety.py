"""Tests for importer.py — import_file safety + no per-track save."""

from pathlib import Path
from unittest.mock import patch

from music_manager.core.config import Paths
from music_manager.core.models import Track
from music_manager.pipeline.importer import import_resolved_track
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.pipeline.importer"


def _track(**overrides) -> Track:
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


# ── import_file failure ───────────────────────────────────────────────────


@patch(f"{_PATCH}.import_file", side_effect=RuntimeError("AppleScript failed"))
@patch(f"{_PATCH}.tag_audio_file")
@patch(f"{_PATCH}.download_track", return_value=("/tmp/song.m4a", 200))
@patch(f"{_PATCH}.search_by_isrc", return_value=[{"url": "https://yt/1"}])
def test_import_file_crash_returns_pending(
    mock_search, mock_dl, mock_tag, mock_import, tmp_path
) -> None:
    """import_file RuntimeError → PendingTrack, not crash."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    albums.put(1, {"cover_url": "https://cover.jpg"})

    result = import_resolved_track(_track(), _paths(tmp_path), tracks, albums)

    assert result is not None
    assert result.reason == "apple_import_failed"
    # Track should NOT be in store
    assert len(tracks.all()) == 0


# ── No per-track save ─────────────────────────────────────────────────────


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}._cleanup")
@patch(f"{_PATCH}.import_file", return_value="APPLE_NEW")
@patch(f"{_PATCH}.tag_audio_file")
@patch(f"{_PATCH}.download_track", return_value=("/tmp/song.m4a", 200))
@patch(f"{_PATCH}.search_by_isrc", return_value=[{"url": "https://yt/1"}])
def test_import_does_not_save_to_disk(
    mock_search, mock_dl, mock_tag, mock_apple, mock_clean, mock_log, tmp_path
) -> None:
    """import_resolved_track adds to store but does NOT save to disk.

    Saves are centralized in menu._switch_view._save_all().
    """
    path = str(tmp_path / "tracks.json")
    tracks = Tracks(path)
    albums = Albums(str(tmp_path / "albums.json"))
    albums.put(1, {"cover_url": "https://cover.jpg"})

    result = import_resolved_track(_track(), _paths(tmp_path), tracks, albums)
    assert result is None  # success

    # Entry is in memory
    assert tracks.get_by_apple_id("APPLE_NEW") is not None

    # But file should NOT exist (no save called)
    # Actually the store was created empty, so if save() was called
    # the file would exist with data
    reload = Tracks(path)
    # If no save, reloading gives empty store
    assert len(reload.all()) == 0
