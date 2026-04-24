"""Tests for services/albums.py."""

from pathlib import Path

from music_manager.services.albums import Albums


def test_put_and_get(tmp_path: Path) -> None:
    """Put saves to disk, get retrieves by int ID."""
    path = str(tmp_path / "albums.json")
    store = Albums(path)
    store.put(12345, {"title": "Thriller"})
    store.save()

    result = Albums(path).get(12345)
    assert result is not None
    assert result["title"] == "Thriller"
