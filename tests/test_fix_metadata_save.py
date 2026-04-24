"""Tests for fix_metadata.py — save behavior + cover detection."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from music_manager.core.models import LibraryEntry
from music_manager.options.fix_metadata import find_all_divergences
from music_manager.services.albums import Albums
from music_manager.services.apple import Apple
from music_manager.services.tracks import Tracks


def _make_stores(tmp_path: Path) -> tuple[Tracks, Albums]:
    """Create tracks + albums stores with one identified track."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))

    tracks.add(
        "A1",
        {
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "deezer_id": 123,
            "album_id": 456,
            "isrc": "ISRC123",
            "status": "done",
        },
    )

    albums.put(
        456,
        {
            "id": 456,
            "title": "Album",
            "artist": "Artist",
            "album_artist": "Artist",
            "genre": "Pop",
            "year": "2020",
            "release_date": "2020-01-01",
            "total_tracks": 10,
            "total_discs": 1,
            "cover_url": "https://cover.jpg",
            "_tracklist": [
                {
                    "id": 123,
                    "title": "Song",
                    "track_position": 1,
                    "disk_number": 1,
                    "artist": {"name": "Artist"},
                }
            ],
        },
    )
    albums.save()

    return tracks, albums


# ── albums_store.save() called ────────────────────────────────────────────


def test_find_divergences_saves_album_cache(tmp_path) -> None:
    """find_all_divergences saves albums_store to disk."""
    tracks, albums = _make_stores(tmp_path)
    prefs_path = str(tmp_path / "prefs.json")

    apple = MagicMock(spec=Apple)
    apple.get_all.return_value = {
        "A1": LibraryEntry(
            apple_id="A1",
            title="Song",
            artist="Artist",
            album="Album",
            genre="Pop",
            year="2020",
            track_number=1,
            disk_number=1,
            total_tracks=10,
            album_artist="Artist",
            has_artwork=True,
        ),
    }

    find_all_divergences(tracks, albums, apple, prefs_path)

    # albums_store should have been saved (dirty flag cleared)
    assert not albums._dirty


# ── Cover detection ───────────────────────────────────────────────────────


def test_missing_cover_detected(tmp_path) -> None:
    """Track without artwork → cover divergence proposed."""
    tracks, albums = _make_stores(tmp_path)
    prefs_path = str(tmp_path / "prefs.json")

    apple = MagicMock(spec=Apple)
    apple.get_all.return_value = {
        "A1": LibraryEntry(
            apple_id="A1",
            title="Song",
            artist="Artist",
            album="Album",
            genre="Pop",
            year="2020",
            track_number=1,
            disk_number=1,
            total_tracks=10,
            album_artist="Artist",
            has_artwork=False,
        ),
    }

    divs = find_all_divergences(tracks, albums, apple, prefs_path)

    assert len(divs) == 1
    cover_divs = [d for d in divs[0].divergences if d.field_name == "cover"]
    assert len(cover_divs) == 1
    assert cover_divs[0].deezer_value == "https://cover.jpg"


@patch("music_manager.options.fix_metadata.get_cover_dimensions", return_value=(3000, 3000))
def test_existing_cover_no_divergence(mock_dims, tmp_path) -> None:
    """Track with artwork and good cover dimensions → no divergence."""
    tracks, albums = _make_stores(tmp_path)
    prefs_path = str(tmp_path / "prefs.json")

    apple = MagicMock(spec=Apple)
    apple.get_all.return_value = {
        "A1": LibraryEntry(
            apple_id="A1",
            title="Song",
            artist="Artist",
            album="Album",
            genre="Pop",
            year="2020",
            track_number=1,
            disk_number=1,
            total_tracks=10,
            album_artist="Artist",
            has_artwork=True,
            file_path="/music/song.m4a",
        ),
    }

    divs = find_all_divergences(tracks, albums, apple, prefs_path)

    assert len(divs) == 0


# ── Tracks without deezer_id skipped ──────────────────────────────────────


def test_baseline_tracks_skipped(tmp_path) -> None:
    """Tracks without deezer_id are skipped (baseline, not identified)."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))

    tracks.add(
        "A1",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "ISRC1",
            "status": None,
            "origin": "baseline",
            # No deezer_id, no album_id
        },
    )

    prefs_path = str(tmp_path / "prefs.json")
    apple = MagicMock(spec=Apple)
    apple.get_all.return_value = {}

    divs = find_all_divergences(tracks, albums, apple, prefs_path)

    assert len(divs) == 0  # skipped, no divergences


# ── Metadata divergence detection ─────────────────────────────────────────


def test_genre_divergence_detected(tmp_path) -> None:
    """Wrong genre in Apple Music → divergence proposed."""
    tracks, albums = _make_stores(tmp_path)
    prefs_path = str(tmp_path / "prefs.json")

    apple = MagicMock(spec=Apple)
    apple.get_all.return_value = {
        "A1": LibraryEntry(
            apple_id="A1",
            title="Song",
            artist="Artist",
            album="Album",
            genre="Wrong Genre",
            year="2020",
            track_number=1,
            disk_number=1,
            total_tracks=10,
            album_artist="Artist",
            has_artwork=True,
        ),
    }

    divs = find_all_divergences(tracks, albums, apple, prefs_path)

    assert len(divs) == 1
    genre_divs = [d for d in divs[0].divergences if d.field_name == "genre"]
    assert len(genre_divs) == 1
    assert genre_divs[0].local_value == "Wrong Genre"
    assert genre_divs[0].deezer_value == "Pop"
