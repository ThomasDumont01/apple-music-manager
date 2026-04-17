"""Tests for core/models.py."""

from music_manager.core.models import Track


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
