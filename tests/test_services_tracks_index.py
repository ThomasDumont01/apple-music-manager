"""Tests for tracks.py — ISRC case-insensitive + index cleanup."""

from pathlib import Path

from music_manager.services.tracks import Tracks

# ── ISRC case-insensitive ─────────────────────────────────────────────────


def test_get_by_isrc_case_insensitive(tmp_path: Path) -> None:
    """Lookup by ISRC is case-insensitive."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "isrc": "USWB12403464"})

    assert store.get_by_isrc("USWB12403464") is not None
    assert store.get_by_isrc("uswb12403464") is not None
    assert store.get_by_isrc("Uswb12403464") is not None


def test_add_stores_isrc_uppercase(tmp_path: Path) -> None:
    """ISRC index stores uppercase regardless of input case."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "isrc": "abc123"})

    assert store.get_by_isrc("ABC123") is not None
    assert store.get_by_isrc("abc123") is not None


def test_update_isrc_case_change(tmp_path: Path) -> None:
    """Changing ISRC case doesn't create duplicate index entries."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "isrc": "ABC123"})
    store.update("A1", {"isrc": "abc123"})

    # Should still find by either case
    entry = store.get_by_isrc("abc123")
    assert entry is not None
    assert entry["isrc"] == "abc123"


# ── Index cleanup on remove ───────────────────────────────────────────────


def test_remove_cleans_isrc_index(tmp_path: Path) -> None:
    """Removing entry cleans ISRC index."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "isrc": "ISRC1"})
    store.remove("A1")

    assert store.get_by_isrc("ISRC1") is None


def test_remove_cleans_title_artist_index(tmp_path: Path) -> None:
    """Removing entry cleans title+artist index."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Bohemian Rhapsody", "artist": "Queen", "isrc": ""})
    store.remove("A1")

    assert store.get_by_title_artist("bohemian rhapsody", "queen") is None


def test_remove_cleans_csv_title_index(tmp_path: Path) -> None:
    """Removing entry cleans csv_title index."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Deezer Title",
            "artist": "Artist",
            "csv_title": "CSV Title",
            "csv_artist": "Artist",
        },
    )
    store.remove("A1")

    assert store.get_by_title_artist("csv title", "artist") is None


def test_remove_doesnt_affect_other_entries(tmp_path: Path) -> None:
    """Removing one entry doesn't break indexes of others."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song1", "artist": "Art", "isrc": "ISRC1"})
    store.add("A2", {"title": "Song2", "artist": "Art", "isrc": "ISRC2"})
    store.remove("A1")

    assert store.get_by_isrc("ISRC1") is None
    assert store.get_by_isrc("ISRC2") is not None
    assert store.get_by_apple_id("A2") is not None


# ── Index cleanup on add (overwrite) ──────────────────────────────────────


def test_add_overwrite_cleans_old_isrc(tmp_path: Path) -> None:
    """Overwriting entry with new ISRC removes old ISRC from index."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "isrc": "OLD_ISRC"})
    store.add("A1", {"title": "Song", "isrc": "NEW_ISRC"})

    assert store.get_by_isrc("OLD_ISRC") is None
    assert store.get_by_isrc("NEW_ISRC") is not None


# ── Update re-indexes title+artist ────────────────────────────────────────


def test_update_title_reindexes(tmp_path: Path) -> None:
    """Updating title re-indexes title+artist lookup."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Old Title", "artist": "Artist", "isrc": ""})

    store.update("A1", {"title": "New Title"})

    assert store.get_by_title_artist("new title", "artist") is not None


def test_update_isrc_cleans_old(tmp_path: Path) -> None:
    """Updating ISRC removes old ISRC from index."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "isrc": "OLD"})

    store.update("A1", {"isrc": "NEW"})

    assert store.get_by_isrc("OLD") is None
    assert store.get_by_isrc("NEW") is not None


# ── Persistence ───────────────────────────────────────────────────────────


def test_indexes_rebuilt_from_disk(tmp_path: Path) -> None:
    """Indexes are rebuilt correctly when loading from disk."""
    path = str(tmp_path / "tracks.json")
    store = Tracks(path)
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "isrc123",
            "csv_title": "CSV Song",
            "csv_artist": "CSV Artist",
        },
    )
    store.save()

    # Reload from disk
    store2 = Tracks(path)
    assert store2.get_by_isrc("ISRC123") is not None
    assert store2.get_by_isrc("isrc123") is not None
    assert store2.get_by_title_artist("song", "artist") is not None
    assert store2.get_by_title_artist("csv song", "csv artist") is not None
