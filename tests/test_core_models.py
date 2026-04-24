"""Tests for core/models.py."""

from music_manager.core.models import Album, LibraryEntry, PendingTrack, Track


def test_from_dict_ignores_unknown_fields() -> None:
    """Unknown keys in JSON data should be silently dropped."""
    data = {
        "isrc": "FRZ032200001",
        "title": "Test",
        "artist": "Artist",
        "album": "Album",
        "unknown_field": 42,  # does not exist on Track
        "removed_in_v2": "x",  # simulates legacy data
    }
    track = Track.from_dict(data)

    assert track.isrc == "FRZ032200001"
    assert track.title == "Test"
    assert not hasattr(track, "unknown_field")


# ── Album ────────────────────────────────────────────────────────────────


def test_album_to_dict():
    """Album serializes to dict."""

    a = Album(id=1, title="Test", artist="Art", cover_url="url")
    d = a.to_dict()
    assert d["title"] == "Test"
    assert d["id"] == 1


def test_album_from_dict():
    """Album deserializes from dict."""

    d = {"id": 1, "title": "Test", "artist": "Art", "cover_url": "url"}
    a = Album.from_dict(d)
    assert a.title == "Test"
    assert a.id == 1


# ── LibraryEntry ─────────────────────────────────────────────────────────


def test_library_entry_fields():
    """LibraryEntry has expected fields."""

    e = LibraryEntry(apple_id="X", title="T", artist="A", album="Al")
    assert e.explicit is False
    assert e.has_artwork is False
    assert e.file_path == ""
    assert e.isrc == ""


def test_library_entry_to_dict():
    """LibraryEntry serializes with all fields."""

    e = LibraryEntry(
        apple_id="X",
        title="T",
        artist="A",
        album="Al",
        explicit=True,
        has_artwork=True,
        file_path="/music/t.m4a",
    )
    d = e.to_dict()
    assert d["explicit"] is True
    assert d["has_artwork"] is True
    assert d["file_path"] == "/music/t.m4a"


# ── PendingTrack ─────────────────────────────────────────────────────────


def test_pending_track_defaults():
    """PendingTrack has sensible defaults."""

    p = PendingTrack(reason="not_found")
    assert p.reason == "not_found"
    assert p.csv_title == ""
    assert p.csv_artist == ""
    assert p.track is None
    assert p.candidates == []


def test_pending_track_with_data():
    """PendingTrack stores CSV metadata."""

    p = PendingTrack(
        reason="ambiguous",
        csv_title="Song",
        csv_artist="Artist",
        csv_album="Album",
    )
    assert p.csv_title == "Song"
    assert p.csv_album == "Album"


# ── Track ────────────────────────────────────────────────────────────────


def test_track_to_dict_roundtrip():
    """Track to_dict preserves all fields."""

    t = Track(
        deezer_id=123,
        title="S",
        artist="A",
        album="Al",
        isrc="ISRC123",
        explicit=True,
        duration=240,
    )
    d = t.to_dict()
    assert d["deezer_id"] == 123
    assert d["explicit"] is True
    assert d["isrc"] == "ISRC123"
