"""Tests for albums.py — dirty flag on remove + save behavior."""

from pathlib import Path

from music_manager.services.albums import Albums


def test_put_sets_dirty(tmp_path: Path) -> None:
    """put() sets dirty flag."""
    store = Albums(str(tmp_path / "albums.json"))
    assert not store._dirty

    store.put(123, {"title": "Album"})
    assert store._dirty


def test_remove_sets_dirty(tmp_path: Path) -> None:
    """remove() sets dirty flag when entry exists."""
    store = Albums(str(tmp_path / "albums.json"))
    store.put(123, {"title": "Album"})
    store.save()  # clear dirty

    assert not store._dirty
    store.remove(123)
    assert store._dirty


def test_remove_nonexistent_not_dirty(tmp_path: Path) -> None:
    """remove() does NOT set dirty if entry doesn't exist."""
    store = Albums(str(tmp_path / "albums.json"))
    store.remove(999)
    assert not store._dirty


def test_save_clears_dirty(tmp_path: Path) -> None:
    """save() writes to disk and clears dirty flag."""
    path = str(tmp_path / "albums.json")
    store = Albums(path)
    store.put(1, {"title": "Test"})
    assert store._dirty

    store.save()
    assert not store._dirty

    # Verify persisted
    reload = Albums(path)
    album = reload.get(1)
    assert album is not None
    assert album["title"] == "Test"


def test_save_noop_when_clean(tmp_path: Path) -> None:
    """save() does nothing when not dirty."""
    path = str(tmp_path / "albums.json")
    store = Albums(path)
    store.save()

    # File should not exist (no data, not dirty)
    assert not (tmp_path / "albums.json").exists()


def test_remove_persisted_after_save(tmp_path: Path) -> None:
    """Removal is persisted to disk after save."""
    path = str(tmp_path / "albums.json")
    store = Albums(path)
    store.put(1, {"title": "A"})
    store.put(2, {"title": "B"})
    store.save()

    store.remove(1)
    store.save()

    reload = Albums(path)
    assert reload.get(1) is None
    assert reload.get(2) is not None
