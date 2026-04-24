"""Integration tests — cross-module data integrity scenarios."""

from pathlib import Path
from unittest.mock import patch

from music_manager.core.config import Paths
from music_manager.core.io import save_csv
from music_manager.core.models import Track
from music_manager.options.import_tracks import process_csv
from music_manager.pipeline.dedup import is_duplicate
from music_manager.services.albums import Albums
from music_manager.services.resolver import ResolveResult
from music_manager.services.tracks import Tracks

_PATCH_IMPORT = "music_manager.options.import_tracks"


def _paths(tmp_path: Path) -> Paths:
    return Paths(str(tmp_path / "data"))


def _track(**overrides) -> Track:
    defaults = {
        "isrc": "ISRC123",
        "title": "Song",
        "artist": "Artist",
        "album": "Album",
        "deezer_id": 1,
        "album_id": 1,
    }
    defaults.update(overrides)
    return Track(**defaults)


# ── Import then re-import: dedup detects ──────────────────────────────────


@patch(f"{_PATCH_IMPORT}.log_event")
@patch(f"{_PATCH_IMPORT}.import_resolved_track", return_value=None)
@patch(f"{_PATCH_IMPORT}.resolve")
def test_import_then_reimport_detected(mock_resolve, mock_import, mock_log, tmp_path) -> None:
    """Second import of same track is skipped."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    csv_path = str(tmp_path / "import.csv")
    save_csv(csv_path, [{"title": "Song", "artist": "Artist", "album": "Al", "isrc": "ISRC1"}])

    track = _track(isrc="ISRC1")
    track.apple_id = "AP1"
    mock_resolve.return_value = ResolveResult("resolved", track=track)

    def fake_import(trk, *args, **kwargs):
        trk.apple_id = "AP1"
        tracks.add(
            "AP1",
            {
                "title": "Song",
                "artist": "Artist",
                "isrc": "ISRC1",
                "status": "done",
                "deezer_id": 1,
            },
        )
        return None

    mock_import.side_effect = fake_import

    # First import
    r1 = process_csv(csv_path, paths, tracks, albums)
    assert r1.imported == 1

    # Re-create CSV (import removes rows)
    save_csv(csv_path, [{"title": "Song", "artist": "Artist", "album": "Al", "isrc": "ISRC1"}])

    # Second import — should be skipped
    r2 = process_csv(csv_path, paths, tracks, albums)
    assert r2.skipped == 1
    assert r2.imported == 0


# ── Failed → retry cycle ─────────────────────────────────────────────────


@patch(f"{_PATCH_IMPORT}.log_event")
@patch(f"{_PATCH_IMPORT}.import_resolved_track")
@patch(f"{_PATCH_IMPORT}.resolve")
def test_failed_track_retried_on_reimport(mock_resolve, mock_import, mock_log, tmp_path) -> None:
    """Track that previously failed is retried on next import."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    # Pre-populate with a failed entry
    tracks.add(
        "OLD",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "ISRC1",
            "status": "failed",
            "deezer_id": 1,
            "apple_id": "OLD",
        },
    )
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    csv_path = str(tmp_path / "import.csv")
    save_csv(csv_path, [{"title": "Song", "artist": "Artist", "album": "Al", "isrc": "ISRC1"}])

    track = _track(isrc="ISRC1")
    mock_resolve.return_value = ResolveResult("resolved", track=track)
    mock_import.return_value = None  # success this time

    result = process_csv(csv_path, paths, tracks, albums)

    # Failed entry should be removed, new import attempted
    assert tracks.get_by_apple_id("OLD") is None
    assert result.imported == 1
    mock_resolve.assert_called_once()


# ── Store consistency after add + remove ──────────────────────────────────


def test_store_consistency_after_operations(tmp_path: Path) -> None:
    """After many add/remove operations, all indexes are consistent."""
    store = Tracks(str(tmp_path / "tracks.json"))

    # Add 10 tracks
    for i in range(10):
        store.add(
            f"A{i}",
            {
                "title": f"Song {i}",
                "artist": f"Art {i}",
                "isrc": f"ISRC{i}",
                "status": "done",
                "deezer_id": 1,
            },
        )

    # Remove even-numbered
    for i in range(0, 10, 2):
        store.remove(f"A{i}")

    # Verify
    assert len(store.all()) == 5
    for i in range(10):
        entry = store.get_by_apple_id(f"A{i}")
        isrc_entry = store.get_by_isrc(f"ISRC{i}")
        if i % 2 == 0:
            assert entry is None, f"A{i} should be removed"
            assert isrc_entry is None, f"ISRC{i} should be cleaned"
        else:
            assert entry is not None, f"A{i} should exist"
            assert isrc_entry is not None, f"ISRC{i} should be indexed"


# ── Dedup with edit_metadata interaction ──────────────────────────────────


def test_dedup_after_metadata_edit(tmp_path: Path) -> None:
    """After editing title via edit_metadata, dedup finds by new title."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Old Title",
            "artist": "Artist",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )

    # Edit title via store.update (same as edit_metadata_track uses)
    store.update("A1", {"title": "New Title"})

    # Dedup should find by new title
    assert is_duplicate("", "New Title", "Artist", store) is True
    # Old title: still found via linear scan (entry has title="New Title" now)
    assert is_duplicate("", "Old Title", "Artist", store) is False


# ── Albums dirty flag propagation ─────────────────────────────────────────


def test_albums_remove_dirty_saved(tmp_path: Path) -> None:
    """albums.remove() sets dirty, save() persists deletion."""
    path = str(tmp_path / "albums.json")
    store = Albums(path)
    store.put(1, {"title": "A"})
    store.put(2, {"title": "B"})
    store.save()

    store.remove(1)
    assert store._dirty
    store.save()

    reload = Albums(path)
    assert reload.get(1) is None
    assert reload.get(2) is not None
