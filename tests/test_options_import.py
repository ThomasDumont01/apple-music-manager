"""Tests for options/import_tracks.py — CSV import pipeline."""

from pathlib import Path
from unittest.mock import patch

from music_manager.core.config import Paths
from music_manager.core.io import load_csv, save_csv
from music_manager.core.models import PendingTrack, Track
from music_manager.options.import_tracks import process_csv
from music_manager.services.albums import Albums
from music_manager.services.resolver import ResolveResult
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.options.import_tracks"


def _paths(tmp_path: Path) -> Paths:
    """Create a Paths object rooted in tmp_path."""
    return Paths(str(tmp_path / "data"))


def _resolved_track(**overrides) -> Track:
    """Create a minimal resolved Track."""
    defaults = {
        "isrc": "ISRC123",
        "title": "Song",
        "artist": "Artist",
        "album": "Album",
        "deezer_id": 1,
        "album_id": 1,
    }
    defaults.update(overrides)
    return Track(**defaults)


# ── process_csv ─────────────────────────────────────────────────────────────


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.import_resolved_track", return_value=None)
@patch(f"{_PATCH}.resolve")
def test_process_csv_imports_resolved(mock_resolve, mock_import, mock_log, tmp_path) -> None:
    """Resolved tracks are imported, row removed from CSV."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    csv_path = str(tmp_path / "import.csv")
    save_csv(csv_path, [{"title": "Song", "artist": "Artist", "album": "Album"}])

    track = _resolved_track()
    mock_resolve.return_value = ResolveResult("resolved", track=track)

    result = process_csv(csv_path, paths, tracks, albums)

    assert result.imported == 1
    assert result.skipped == 0
    assert len(result.pending) == 0
    mock_import.assert_called_once()


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.resolve")
def test_process_csv_skips_duplicates(mock_resolve, mock_log, tmp_path) -> None:
    """Duplicate tracks (already in store with status done) are skipped."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "existing",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "ISRC123",
            "status": "done",
            "deezer_id": 1,
        },
    )

    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    csv_path = str(tmp_path / "import.csv")
    save_csv(
        csv_path, [{"title": "Song", "artist": "Artist", "album": "Album", "isrc": "ISRC123"}]
    )

    result = process_csv(csv_path, paths, tracks, albums)

    assert result.skipped == 1
    assert result.imported == 0
    mock_resolve.assert_not_called()


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.resolve")
def test_process_csv_collects_pending_not_found(mock_resolve, mock_log, tmp_path) -> None:
    """Unresolved tracks become pending."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    csv_path = str(tmp_path / "import.csv")
    save_csv(csv_path, [{"title": "Unknown", "artist": "Nobody", "album": ""}])

    mock_resolve.return_value = ResolveResult("not_found")

    result = process_csv(csv_path, paths, tracks, albums)

    assert result.imported == 0
    assert len(result.pending) == 1
    assert result.pending[0].reason == "not_found"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.import_resolved_track")
@patch(f"{_PATCH}.resolve")
def test_process_csv_collects_pending_import_fail(
    mock_resolve, mock_import, mock_log, tmp_path
) -> None:
    """When import_resolved_track returns PendingTrack, it's collected."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    csv_path = str(tmp_path / "import.csv")
    save_csv(csv_path, [{"title": "Song", "artist": "Artist", "album": "Album"}])

    track = _resolved_track()
    mock_resolve.return_value = ResolveResult("resolved", track=track)
    mock_import.return_value = PendingTrack(reason="youtube_failed", csv_title="Song")

    result = process_csv(csv_path, paths, tracks, albums)

    assert result.imported == 0
    assert len(result.pending) == 1
    assert result.pending[0].reason == "youtube_failed"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.import_resolved_track", return_value=None)
@patch(f"{_PATCH}.resolve")
def test_process_csv_removes_imported_rows(mock_resolve, mock_import, mock_log, tmp_path) -> None:
    """Successfully imported rows are removed from CSV file."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    csv_path = str(tmp_path / "import.csv")
    save_csv(
        csv_path,
        [
            {"title": "Song1", "artist": "A1", "album": "Al1"},
            {"title": "Song2", "artist": "A2", "album": "Al2"},
        ],
    )

    track = _resolved_track()
    # First call: resolved + imported. Second call: not_found → stays in CSV.
    mock_resolve.side_effect = [
        ResolveResult("resolved", track=track),
        ResolveResult("not_found"),
    ]

    process_csv(csv_path, paths, tracks, albums)

    remaining = load_csv(csv_path)
    assert len(remaining) == 1
    assert remaining[0]["title"] == "Song2"


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.resolve")
def test_process_csv_retries_failed(mock_resolve, mock_log, tmp_path) -> None:
    """Failed tracks are removed from store and retried."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "old",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "ISRC123",
            "status": "failed",
            "deezer_id": 1,
            "apple_id": "old",
        },
    )

    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    csv_path = str(tmp_path / "import.csv")
    save_csv(
        csv_path, [{"title": "Song", "artist": "Artist", "album": "Album", "isrc": "ISRC123"}]
    )

    mock_resolve.return_value = ResolveResult("not_found")

    process_csv(csv_path, paths, tracks, albums)

    # Failed entry should have been removed before resolve
    assert tracks.get_by_apple_id("old") is None
    mock_resolve.assert_called_once()


def test_process_csv_empty_file(tmp_path) -> None:
    """Empty CSV returns zero counts."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    csv_path = str(tmp_path / "import.csv")
    with open(csv_path, "w") as f:
        f.write("title,artist,album\n")

    result = process_csv(csv_path, paths, tracks, albums)
    assert result.imported == 0
    assert result.skipped == 0
    assert len(result.pending) == 0
