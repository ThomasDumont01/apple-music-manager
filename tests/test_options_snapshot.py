"""Tests for options/snapshot.py — promote imported tracks to baseline."""

from pathlib import Path

from music_manager.options.snapshot import snapshot
from music_manager.services.tracks import Tracks


def test_snapshot_promotes_imported_done(tmp_path: Path) -> None:
    """Tracks with origin=imported + status=done become baseline."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Song 1", "origin": "imported", "status": "done"})
    tracks.add("A2", {"title": "Song 2", "origin": "imported", "status": "done"})

    count = snapshot(tracks)

    assert count == 2
    entry_a1 = tracks.get_by_apple_id("A1")
    assert entry_a1 is not None
    assert entry_a1["origin"] == "baseline"
    entry_a2 = tracks.get_by_apple_id("A2")
    assert entry_a2 is not None
    assert entry_a2["origin"] == "baseline"


def test_snapshot_ignores_baseline(tmp_path: Path) -> None:
    """Already baseline tracks are not counted."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Song", "origin": "baseline", "status": "done"})

    count = snapshot(tracks)
    assert count == 0


def test_snapshot_ignores_imported_not_done(tmp_path: Path) -> None:
    """Imported tracks without status=done are not promoted."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Song", "origin": "imported", "status": "failed"})
    tracks.add("A2", {"title": "Song 2", "origin": "imported", "status": None})

    count = snapshot(tracks)
    assert count == 0


def test_snapshot_empty_store(tmp_path: Path) -> None:
    """Empty store returns 0."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    count = snapshot(tracks)
    assert count == 0


def test_snapshot_saves_only_when_changes(tmp_path: Path) -> None:
    """Snapshot does not save when nothing to promote."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Song", "origin": "baseline"})
    tracks.save()  # reset dirty flag

    snapshot(tracks)

    # Verify dirty flag was not set (no save triggered)
    assert not tracks._dirty
