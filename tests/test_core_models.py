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
    # Usage stats default to "no signal"
    assert e.loved is False
    assert e.play_count == 0
    assert e.last_played == ""
    assert e.added_date == ""


def test_library_entry_usage_stats_roundtrip():
    """Usage stats survive to_dict / from_dict roundtrip."""

    e = LibraryEntry(
        apple_id="X",
        title="T",
        artist="A",
        album="Al",
        loved=True,
        play_count=42,
        last_played="2025-12-01 10:00:00 +0000",
        added_date="2024-06-15 09:30:00 +0000",
    )
    restored = LibraryEntry.from_dict(e.to_dict())
    assert restored.loved is True
    assert restored.play_count == 42
    assert restored.last_played == "2025-12-01 10:00:00 +0000"
    assert restored.added_date == "2024-06-15 09:30:00 +0000"


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


def test_track_usage_stats_defaults():
    """Track usage stats default to neutral values."""

    t = Track(isrc="X", title="T", artist="A", album="Al")
    assert t.loved is False
    assert t.play_count == 0
    assert t.last_played == ""
    assert t.added_date == ""


def test_track_usage_stats_roundtrip():
    """Track usage stats survive to_dict / from_dict roundtrip."""

    t = Track(
        isrc="X",
        title="T",
        artist="A",
        album="Al",
        loved=True,
        play_count=128,
        last_played="2025-11-20",
        added_date="2024-01-01",
    )
    restored = Track.from_dict(t.to_dict())
    assert restored.loved is True
    assert restored.play_count == 128
    assert restored.last_played == "2025-11-20"
    assert restored.added_date == "2024-01-01"
