"""Tests for services/tracks.py."""

from pathlib import Path

from music_manager.services.tracks import Tracks


def test_add_and_get_by_apple_id(tmp_path: Path) -> None:
    """Add an entry, retrieve by apple_id."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "isrc": "ISRC123"})

    entry = store.get_by_apple_id("A1")
    assert entry is not None
    assert entry["title"] == "Song"


def test_get_by_isrc(tmp_path: Path) -> None:
    """Secondary ISRC index works."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "isrc": "ISRC123"})

    entry = store.get_by_isrc("ISRC123")
    assert entry is not None
    assert entry["title"] == "Song"
    assert store.get_by_isrc("UNKNOWN") is None


def test_update_entry(tmp_path: Path) -> None:
    """Update modifies fields in place."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "genre": ""})
    store.update("A1", {"genre": "Rock"})

    entry = store.get_by_apple_id("A1")
    assert entry is not None
    assert entry["genre"] == "Rock"


def test_update_isrc_rebuilds_index(tmp_path: Path) -> None:
    """Adding ISRC via update makes it findable by ISRC."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "isrc": ""})
    store.update("A1", {"isrc": "NEW123"})

    entry = store.get_by_isrc("NEW123")
    assert entry is not None
    assert entry["title"] == "Song"


def test_without_isrc(tmp_path: Path) -> None:
    """without_isrc returns only entries missing ISRC."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Has", "isrc": "ISRC1"})
    store.add("A2", {"title": "Missing", "isrc": ""})
    store.add("A3", {"title": "Also missing"})

    result = store.without_isrc()
    assert len(result) == 2


def test_save_only_when_dirty(tmp_path: Path) -> None:
    """Save writes only if modified."""
    path = str(tmp_path / "tracks.json")
    store = Tracks(path)
    store.save()  # not dirty → no write
    assert not (tmp_path / "tracks.json").exists()

    store.add("A1", {"title": "Song"})
    store.save()  # dirty → writes
    assert (tmp_path / "tracks.json").exists()
