"""Tests for options/modify_track.py — search, metadata, covers."""

from pathlib import Path
from unittest.mock import patch

from music_manager.options.modify_track import (
    TrackMatch,
    edit_metadata_album,
    edit_metadata_track,
    search_covers,
    search_library,
)
from music_manager.services.tracks import Tracks

# ── search_library ────────────────────────────────────────────────────────


def test_search_by_title(tmp_path: Path) -> None:
    """Search returns tracks matching title."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
            "album": "News",
            "deezer_id": 1,
        },
    )
    store.add("A2", {"title": "Somebody", "artist": "Queen", "album": "Album", "deezer_id": 1})

    tracks, albums = search_library("bohemian", store)

    assert len(tracks) == 1
    assert tracks[0].title == "Bohemian Rhapsody"


def test_search_by_artist(tmp_path: Path) -> None:
    """Search returns tracks matching artist."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song1", "artist": "Taylor Swift", "album": "Al1", "deezer_id": 1})
    store.add("A2", {"title": "Song2", "artist": "Ed Sheeran", "album": "Al2", "deezer_id": 1})

    tracks, albums = search_library("taylor", store)

    assert len(tracks) == 1
    assert tracks[0].artist == "Taylor Swift"


def test_search_albums_grouped(tmp_path: Path) -> None:
    """Search returns albums with track count."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Track1", "artist": "Art", "album": "From Zero", "deezer_id": 1})
    store.add("A2", {"title": "Track2", "artist": "Art", "album": "From Zero", "deezer_id": 1})
    store.add("A3", {"title": "Track3", "artist": "Art", "album": "Other", "deezer_id": 1})

    tracks, albums = search_library("from zero", store)

    assert len(albums) == 1
    assert albums[0].album_title == "From Zero"
    assert albums[0].track_count == 2


def test_search_min_length(tmp_path: Path) -> None:
    """Search requires at least 2 characters."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "A Song", "artist": "Artist", "album": "Album", "deezer_id": 1})

    tracks, albums = search_library("a", store)
    assert tracks == []
    assert albums == []


def test_search_scoring_title_starts_first(tmp_path: Path) -> None:
    """Title starting with query ranked before contains."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "My Song Remix", "artist": "Art", "album": "", "deezer_id": 1})
    store.add("A2", {"title": "Song Title", "artist": "Art", "album": "", "deezer_id": 1})

    tracks, _ = search_library("song", store)

    assert len(tracks) == 2
    assert tracks[0].title == "Song Title"  # starts with → first
    assert tracks[1].title == "My Song Remix"  # contains → second


def test_search_max_results(tmp_path: Path) -> None:
    """Search limits results to MAX_TRACKS and MAX_ALBUMS."""
    store = Tracks(str(tmp_path / "tracks.json"))
    for i in range(20):
        store.add(f"A{i}", {"title": f"Song {i}", "artist": "Artist", "album": "Album"})

    tracks, albums = search_library("song", store)

    assert len(tracks) <= 10  # _MAX_TRACKS
    assert len(albums) <= 5  # _MAX_ALBUMS


# ── edit_metadata_track ───────────────────────────────────────────────────


def test_edit_metadata_track_updates_store(tmp_path: Path) -> None:
    """edit_metadata_track updates entry in tracks.json."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Old Title",
            "artist": "Artist",
            "album": "Album",
            "genre": "Pop",
            "deezer_id": 1,
        },
    )

    with patch("music_manager.services.apple.update_track"):
        result = edit_metadata_track("A1", {"title": "New Title", "genre": "Rock"}, store)

    assert result.success is True
    entry = store.get_by_apple_id("A1")
    assert entry is not None
    assert entry["title"] == "New Title"
    assert entry["genre"] == "Rock"
    assert entry["artist"] == "Artist"  # unchanged


def test_edit_metadata_track_not_found(tmp_path: Path) -> None:
    """edit_metadata_track returns error if track not in store."""
    store = Tracks(str(tmp_path / "tracks.json"))

    result = edit_metadata_track("NONEXISTENT", {"title": "X"}, store)
    assert result.success is False
    assert result.error == "track_not_found"


def test_edit_metadata_track_no_fields(tmp_path: Path) -> None:
    """edit_metadata_track returns error if no fields provided."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "deezer_id": 1})

    result = edit_metadata_track("A1", {}, store)
    assert result.success is False
    assert result.error == "no_fields"


# ── edit_metadata_album ───────────────────────────────────────────────────


def test_edit_metadata_album_updates_all_tracks(tmp_path: Path) -> None:
    """edit_metadata_album updates all tracks in album."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "T1", "artist": "Art", "genre": "Pop", "deezer_id": 1})
    store.add("A2", {"title": "T2", "artist": "Art", "genre": "Pop", "deezer_id": 1})

    album_tracks = [
        TrackMatch(apple_id="A1", title="T1", artist="Art", album="Al", isrc="", deezer_id=0),
        TrackMatch(apple_id="A2", title="T2", artist="Art", album="Al", isrc="", deezer_id=0),
    ]

    with patch("music_manager.services.apple.update_track"):
        result = edit_metadata_album(album_tracks, {"genre": "Rock"}, store)

    assert result.success is True
    a1 = store.get_by_apple_id("A1")
    a2 = store.get_by_apple_id("A2")
    assert a1 is not None
    assert a2 is not None
    assert a1["genre"] == "Rock"
    assert a2["genre"] == "Rock"


# ── search_covers ─────────────────────────────────────────────────────────


def test_search_covers_returns_empty_on_no_album(tmp_path: Path) -> None:
    """Empty album title returns empty list."""
    result = search_covers("", "Artist")
    assert result == []
