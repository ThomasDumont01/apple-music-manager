"""Tests for import_tracks.py — ISRC case handling + find_apple_id."""

from pathlib import Path
from unittest.mock import patch

from music_manager.core.config import Paths
from music_manager.core.io import save_csv
from music_manager.options.import_tracks import find_apple_id, process_csv
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.options.import_tracks"


def _paths(tmp_path: Path) -> Paths:
    return Paths(str(tmp_path / "data"))


# ── ISRC uppercase from CSV ───────────────────────────────────────────────


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.resolve")
def test_csv_lowercase_isrc_detected_as_duplicate(mock_resolve, mock_log, tmp_path) -> None:
    """CSV with lowercase ISRC should match uppercase ISRC in store."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "A1",
        {
            "title": "Canon de Pachelbel",
            "artist": "Altamirano",
            "isrc": "USHM91068209",
            "status": "done",
            "deezer_id": 1,
        },
    )
    albums = Albums(str(tmp_path / "albums.json"))

    csv_path = str(tmp_path / "import.csv")
    save_csv(
        csv_path,
        [
            {
                "title": "Canon de Pachelbel",
                "artist": "Altamirano",
                "album": "Inspiracion",
                "isrc": "ushm91068209",
            }
        ],
    )

    result = process_csv(csv_path, _paths(tmp_path), tracks, albums)

    assert result.skipped == 1
    assert result.imported == 0
    mock_resolve.assert_not_called()


# ── find_apple_id ────────────────────────────────────────────────────────


def testfind_apple_id_by_isrc(tmp_path: Path) -> None:
    """Find apple_id by ISRC match."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "APPLE1",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "ISRC123",
            "apple_id": "APPLE1",
        },
    )

    result = find_apple_id("ISRC123", "Song", "Artist", tracks)
    assert result == "APPLE1"


def testfind_apple_id_by_isrc_case_insensitive(tmp_path: Path) -> None:
    """Find apple_id by ISRC regardless of case."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "APPLE1",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "ISRC123",
            "apple_id": "APPLE1",
        },
    )

    result = find_apple_id("isrc123", "Song", "Artist", tracks)
    assert result == "APPLE1"


def testfind_apple_id_by_title_artist(tmp_path: Path) -> None:
    """Find apple_id by title+artist when no ISRC."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "APPLE1",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "",
            "apple_id": "APPLE1",
        },
    )

    result = find_apple_id("", "Song", "Artist", tracks)
    assert result == "APPLE1"


def testfind_apple_id_isrc_conflict_skips(tmp_path: Path) -> None:
    """Different ISRCs → don't match even if title is similar."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "APPLE1",
        {
            "title": "Dog Days Are Over",
            "artist": "Florence + The Machine",
            "isrc": "GBUM70905782",
            "apple_id": "APPLE1",
        },
    )

    # Different ISRC → not found
    result = find_apple_id("GBUM70900209", "Dog Days Are Over", "Florence + The Machine", tracks)
    assert result == ""
