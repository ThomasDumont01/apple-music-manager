"""Tests for options/fix_metadata.py — divergence detection and corrections."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from music_manager.core.io import load_json, save_json
from music_manager.core.models import LibraryEntry
from music_manager.options.fix_metadata import (
    Divergence,
    apply_corrections,
    find_all_divergences,
    ignore_album,
    save_refusals,
)
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.options.fix_metadata"


def _make_apple(entries: dict[str, LibraryEntry]) -> MagicMock:
    """Create a mock Apple store returning given entries."""
    apple = MagicMock()
    apple.get_all.return_value = entries
    return apple


# ── find_all_divergences ────────────────────────────────────────────────────


def test_find_divergences_detects_title_diff(tmp_path: Path) -> None:
    """Detects title divergence between local and Deezer."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "A1",
        {
            "title": "Bohemain Rhapsody",  # typo
            "artist": "Queen",
            "album": "A Night at the Opera",
            "deezer_id": 123,
            "album_id": 1,
        },
    )

    albums = Albums(str(tmp_path / "albums.json"))
    prefs_path = str(tmp_path / "prefs.json")
    save_json(prefs_path, {})

    apple = _make_apple(
        {
            "A1": LibraryEntry(
                apple_id="A1",
                title="Bohemain Rhapsody",
                artist="Queen",
                album="A Night at the Opera",
            )
        }
    )

    album_data = {
        "title": "A Night at the Opera",
        "artist": "Queen",
        "total_tracks": 12,
        "cover_url": "",
        "genre": "Rock",
        "year": "1975",
        "album_artist": "Queen",
    }
    tracklist = {
        "data": [
            {
                "id": 123,
                "title": "Bohemian Rhapsody",
                "artist": {"name": "Queen"},
                "track_position": 11,
                "disk_number": 1,
            }
        ]
    }

    with (
        patch(f"{_PATCH}.fetch_album_with_cover", return_value=album_data),
        patch(f"{_PATCH}.deezer_get", return_value=tracklist),
    ):
        result = find_all_divergences(tracks, albums, apple, prefs_path)

    assert len(result) == 1
    divs = result[0].divergences
    title_divs = [d for d in divs if d.field_name == "title"]
    assert len(title_divs) == 1
    assert title_divs[0].local_value == "Bohemain Rhapsody"
    assert title_divs[0].deezer_value == "Bohemian Rhapsody"


def test_find_divergences_no_diff_when_matching(tmp_path: Path) -> None:
    """No divergences when local matches Deezer."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "A1",
        {
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
            "album": "A Night at the Opera",
            "deezer_id": 123,
            "album_id": 1,
        },
    )

    albums = Albums(str(tmp_path / "albums.json"))
    prefs_path = str(tmp_path / "prefs.json")
    save_json(prefs_path, {})

    apple = _make_apple(
        {
            "A1": LibraryEntry(
                apple_id="A1",
                title="Bohemian Rhapsody",
                artist="Queen",
                album="A Night at the Opera",
                genre="Rock",
                year="1975",
                track_number=11,
                disk_number=1,
                total_tracks=12,
                album_artist="Queen",
            )
        }
    )

    album_data = {
        "title": "A Night at the Opera",
        "artist": "Queen",
        "total_tracks": 12,
        "cover_url": "",
        "genre": "Rock",
        "year": "1975",
        "album_artist": "Queen",
    }
    tracklist = {
        "data": [
            {
                "id": 123,
                "title": "Bohemian Rhapsody",
                "artist": {"name": "Queen"},
                "track_position": 11,
                "disk_number": 1,
            }
        ]
    }

    with (
        patch(f"{_PATCH}.fetch_album_with_cover", return_value=album_data),
        patch(f"{_PATCH}.deezer_get", return_value=tracklist),
    ):
        result = find_all_divergences(tracks, albums, apple, prefs_path)

    assert len(result) == 0


def test_find_divergences_skips_refused(tmp_path: Path) -> None:
    """Already-refused corrections are filtered out."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "A1",
        {
            "title": "Wrong Title",
            "artist": "Queen",
            "album": "Album",
            "deezer_id": 123,
            "album_id": 1,
        },
    )

    albums = Albums(str(tmp_path / "albums.json"))
    prefs_path = str(tmp_path / "prefs.json")
    save_json(prefs_path, {"refusals": {"A1:title": "Right Title"}})

    apple = _make_apple(
        {
            "A1": LibraryEntry(
                apple_id="A1",
                title="Wrong Title",
                artist="Queen",
                album="Album",
                track_number=1,
                disk_number=1,
                total_tracks=1,
                album_artist="Queen",
            )
        }
    )

    album_data = {
        "title": "Album",
        "artist": "Queen",
        "total_tracks": 1,
        "cover_url": "",
        "genre": "",
        "year": "",
        "album_artist": "Queen",
    }
    tracklist = {
        "data": [
            {
                "id": 123,
                "title": "Right Title",
                "artist": {"name": "Queen"},
                "track_position": 1,
                "disk_number": 1,
            }
        ]
    }

    with (
        patch(f"{_PATCH}.fetch_album_with_cover", return_value=album_data),
        patch(f"{_PATCH}.deezer_get", return_value=tracklist),
    ):
        result = find_all_divergences(tracks, albums, apple, prefs_path)

    assert len(result) == 0


def test_find_divergences_skips_ignored_album(tmp_path: Path) -> None:
    """Ignored albums are skipped entirely."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "A1",
        {
            "title": "Song",
            "artist": "Artist",
            "album": "Ignored Album",
            "deezer_id": 123,
            "album_id": 1,
        },
    )

    albums = Albums(str(tmp_path / "albums.json"))
    prefs_path = str(tmp_path / "prefs.json")
    save_json(prefs_path, {"ignored_albums": ["Ignored Album"]})

    apple = _make_apple(
        {"A1": LibraryEntry(apple_id="A1", title="Song", artist="Artist", album="Ignored Album")}
    )

    album_data = {
        "title": "Ignored Album",
        "artist": "Artist",
        "total_tracks": 1,
        "cover_url": "",
        "genre": "",
        "year": "",
        "album_artist": "",
    }

    with patch(f"{_PATCH}.fetch_album_with_cover", return_value=album_data):
        result = find_all_divergences(tracks, albums, apple, prefs_path)

    assert len(result) == 0


def test_find_divergences_skips_unidentified(tmp_path: Path) -> None:
    """Tracks without deezer_id are skipped."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Song", "artist": "Artist", "album": "Album"})

    albums = Albums(str(tmp_path / "albums.json"))
    prefs_path = str(tmp_path / "prefs.json")
    save_json(prefs_path, {})
    apple = _make_apple({})

    result = find_all_divergences(tracks, albums, apple, prefs_path)
    assert len(result) == 0


# ── apply_corrections ──────────────────────────────────────────────────────


@patch(f"{_PATCH}.update_tracks_batch")
def test_apply_corrections_updates_track(mock_update, tmp_path: Path) -> None:
    """apply_corrections calls update_track and updates store."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Wrong", "artist": "Queen"})

    corrections = [Divergence("A1", "title", "Wrong", "Right")]
    count, _ = apply_corrections(corrections, tracks)

    assert count == 1
    mock_update.assert_called_once()
    batch = mock_update.call_args[0][0]
    assert batch["A1"]["title"] == "Right"
    entry = tracks.get_by_apple_id("A1")
    assert entry is not None
    assert entry["title"] == "Right"


@patch(f"{_PATCH}.update_tracks_batch")
def test_apply_corrections_int_fields(mock_update, tmp_path: Path) -> None:
    """Int fields (year, track_number) are converted before AppleScript call."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Song", "year": "2000"})

    corrections = [Divergence("A1", "year", "2000", "1999")]
    count, _ = apply_corrections(corrections, tracks)

    assert count == 1
    batch = mock_update.call_args[0][0]
    assert batch["A1"]["year"] == 1999


# ── save_refusals ──────────────────────────────────────────────────────────


def test_save_refusals_persists(tmp_path: Path) -> None:
    """Refused corrections are saved to preferences file."""
    prefs_path = str(tmp_path / "prefs.json")
    save_json(prefs_path, {})

    refused = [Divergence("A1", "title", "Local", "Deezer")]
    save_refusals(refused, prefs_path)

    prefs = load_json(prefs_path)
    assert prefs["refusals"]["A1:title"] == "Deezer"


# ── ignore_album ────────────────────────────────────────────────────────────


def test_ignore_album_persists(tmp_path: Path) -> None:
    """Ignored album is saved to preferences."""
    prefs_path = str(tmp_path / "prefs.json")
    save_json(prefs_path, {})

    ignore_album("Bad Album", prefs_path)

    prefs = load_json(prefs_path)
    assert "Bad Album" in prefs["ignored_albums"]


def test_ignore_album_no_duplicate(tmp_path: Path) -> None:
    """Ignoring same album twice does not create duplicate entry."""
    prefs_path = str(tmp_path / "prefs.json")
    save_json(prefs_path, {"ignored_albums": ["Album"]})

    ignore_album("Album", prefs_path)

    prefs = load_json(prefs_path)
    assert prefs["ignored_albums"].count("Album") == 1
