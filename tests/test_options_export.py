"""Tests for options/export.py — playlist export to CSV."""

from pathlib import Path

from music_manager.core.io import load_csv
from music_manager.options.export import export_playlist


def test_export_playlist_writes_csv(tmp_path: Path) -> None:
    """Tracks are exported with all metadata fields."""
    filepath = str(tmp_path / "playlist.csv")
    tracks = [
        {
            "title": "Song 1",
            "artist": "Artist 1",
            "album": "Album 1",
            "genre": "Rock",
            "year": "1975",
            "duration": "354",
            "track_number": "11",
            "disk_number": "1",
            "album_artist": "Artist 1",
            "isrc": "ISRC123",
        },
        {
            "title": "Song 2",
            "artist": "Artist 2",
            "album": "Album 2",
        },
    ]

    count = export_playlist(tracks, filepath)

    assert count == 2
    rows = load_csv(filepath)
    assert len(rows) == 2
    assert rows[0]["title"] == "Song 1"
    assert rows[0]["artist"] == "Artist 1"
    assert rows[1]["title"] == "Song 2"


def test_export_empty_list(tmp_path: Path) -> None:
    """Exporting empty list creates CSV with headers only."""
    filepath = str(tmp_path / "empty.csv")
    count = export_playlist([], filepath)

    assert count == 0
    rows = load_csv(filepath)
    assert len(rows) == 0


def test_export_preserves_special_chars(tmp_path: Path) -> None:
    """Special characters (accents, quotes) survive roundtrip."""
    filepath = str(tmp_path / "special.csv")
    tracks = [{"title": "L'été indien", "artist": "Joe Dassin", "album": 'L"album'}]

    export_playlist(tracks, filepath)
    rows = load_csv(filepath)

    assert rows[0]["title"] == "L'été indien"
    assert rows[0]["artist"] == "Joe Dassin"
