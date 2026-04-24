"""Tests for ui/app.py — auto_sync logic."""

from pathlib import Path
from unittest.mock import MagicMock

from music_manager.core.models import LibraryEntry
from music_manager.services.albums import Albums
from music_manager.services.apple import Apple
from music_manager.services.tracks import Tracks
from music_manager.ui.app import MusicApp

# ── auto_sync ─────────────────────────────────────────────────────────────


def test_auto_sync_adds_new_tracks(tmp_path: Path) -> None:
    """Tracks in Apple Music but not in store → added as baseline."""

    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))
    apple = MagicMock(spec=Apple)
    apple.get_all.return_value = {
        "AP1": LibraryEntry(apple_id="AP1", title="New Song", artist="Art", album="Al"),
    }

    app = MusicApp(tracks_store=tracks, albums_store=albums, apple=apple)
    app._auto_sync(apple, tracks)

    entry = tracks.get_by_apple_id("AP1")
    assert entry is not None
    assert entry["title"] == "New Song"
    assert entry["origin"] == "baseline"
    assert entry["status"] is None


def test_auto_sync_removes_orphan_tracks(tmp_path: Path) -> None:
    """Tracks in store but not in Apple Music → removed."""

    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("ORPHAN", {"title": "Gone", "isrc": "X", "status": "done"})
    albums = Albums(str(tmp_path / "a.json"))
    apple = MagicMock(spec=Apple)
    apple.get_all.return_value = {}  # empty library

    app = MusicApp(tracks_store=tracks, albums_store=albums, apple=apple)
    app._auto_sync(apple, tracks)

    assert tracks.get_by_apple_id("ORPHAN") is None
    assert len(tracks.all()) == 0


def test_auto_sync_preserves_enriched_entries(tmp_path: Path) -> None:
    """Existing enriched entries (deezer_id etc) are NOT overwritten."""

    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "AP1",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "ISRC1",
            "deezer_id": 42,
            "album_id": 10,
            "status": "done",
        },
    )
    albums = Albums(str(tmp_path / "a.json"))
    apple = MagicMock(spec=Apple)
    apple.get_all.return_value = {
        "AP1": LibraryEntry(apple_id="AP1", title="Song", artist="Art", album="Al"),
    }

    app = MusicApp(tracks_store=tracks, albums_store=albums, apple=apple)
    app._auto_sync(apple, tracks)

    entry = tracks.get_by_apple_id("AP1")
    assert entry is not None
    assert entry["deezer_id"] == 42  # preserved, not overwritten
    assert entry["album_id"] == 10


def test_auto_sync_no_changes(tmp_path: Path) -> None:
    """When store matches Apple → no changes, no save."""

    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("AP1", {"title": "Song", "artist": "Art", "isrc": "", "origin": "baseline"})
    albums = Albums(str(tmp_path / "a.json"))
    apple = MagicMock(spec=Apple)
    apple.get_all.return_value = {
        "AP1": LibraryEntry(apple_id="AP1", title="Song", artist="Art", album="Al"),
    }

    app = MusicApp(tracks_store=tracks, albums_store=albums, apple=apple)
    app._auto_sync(apple, tracks)

    assert len(tracks.all()) == 1  # unchanged


# ── cleanup_orphan_albums ─────────────────────────────────────────────────


def test_cleanup_orphan_albums(tmp_path: Path) -> None:
    """Albums not referenced by any track are removed."""

    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("AP1", {"title": "Song", "album_id": 1})
    albums = Albums(str(tmp_path / "a.json"))
    albums.put(1, {"title": "Referenced"})
    albums.put(2, {"title": "Orphan"})
    albums.save()

    app = MusicApp(tracks_store=tracks, albums_store=albums)
    app._cleanup_orphan_albums(tracks)

    assert albums.get(1) is not None
    assert albums.get(2) is None  # removed


def test_cleanup_orphan_albums_empty_store(tmp_path: Path) -> None:
    """No tracks → all albums removed."""

    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))
    albums.put(1, {"title": "A"})
    albums.put(2, {"title": "B"})
    albums.save()

    app = MusicApp(tracks_store=tracks, albums_store=albums)
    app._cleanup_orphan_albums(tracks)

    assert albums.get(1) is None
    assert albums.get(2) is None


def test_auto_sync_updates_file_path(tmp_path: Path) -> None:
    """_auto_sync updates stale file_path from Apple scan."""
    app = MusicApp.__new__(MusicApp)
    app.albums_store = None
    app.paths = None

    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "A1",
        {
            "title": "Song",
            "artist": "Art",
            "file_path": "/old/path/song.m4a",
            "deezer_id": 1,
        },
    )
    tracks.save()

    apple = MagicMock()
    apple.get_all.return_value = {
        "A1": LibraryEntry(
            apple_id="A1",
            title="Song",
            artist="Art",
            album="Al",
            file_path="/new/path/song.m4a",
        ),
    }

    app._auto_sync(apple, tracks)

    assert tracks.all()["A1"]["file_path"] == "/new/path/song.m4a"
