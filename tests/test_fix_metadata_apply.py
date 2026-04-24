"""Tests for fix_metadata.py — apply_corrections full flow."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from music_manager.core.io import load_json, save_json
from music_manager.core.models import LibraryEntry
from music_manager.options.fix_metadata import (
    Divergence,
    apply_corrections,
    save_refusals,
)
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.options.fix_metadata"


# ── apply_corrections: metadata ───────────────────────────────────────────


@patch(f"{_PATCH}.update_tracks_batch")
def test_apply_multiple_fields_batched(mock_update, tmp_path: Path) -> None:
    """Multiple fields for same track → one update_track call."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Old", "genre": "Pop"})

    corrections = [
        Divergence("A1", "title", "Old", "New"),
        Divergence("A1", "genre", "Pop", "Rock"),
    ]
    count, _ = apply_corrections(corrections, tracks)

    assert count == 2
    mock_update.assert_called_once()  # batched into one call
    batch = mock_update.call_args[0][0]  # dict[apple_id, fields]
    assert "A1" in batch
    assert batch["A1"]["title"] == "New"
    assert batch["A1"]["genre"] == "Rock"


@patch(f"{_PATCH}.update_tracks_batch")
def test_apply_int_conversion(mock_update, tmp_path: Path) -> None:
    """Int fields converted: year, track_number, total_tracks, disk_number."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S", "year": "2000", "track_number": "1"})

    corrections = [
        Divergence("A1", "year", "2000", "1999"),
        Divergence("A1", "track_number", "1", "5"),
    ]
    count, _ = apply_corrections(corrections, tracks)

    assert count == 2
    batch = mock_update.call_args[0][0]
    assert batch["A1"]["year"] == 1999
    assert batch["A1"]["track_number"] == 5


@patch(f"{_PATCH}.update_tracks_batch")
def test_apply_invalid_int_field_skipped(mock_update, tmp_path: Path) -> None:
    """Non-numeric value for int field → correction silently skipped."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S", "year": ""})

    corrections = [Divergence("A1", "year", "", "unknown")]
    count, _ = apply_corrections(corrections, tracks)

    assert count == 0  # _to_apple_value returns {}, apple_fields empty
    mock_update.assert_not_called()


@patch(f"{_PATCH}.update_tracks_batch")
def test_apply_updates_store_via_update(mock_update, tmp_path: Path) -> None:
    """apply_corrections updates tracks_store (dirty flag + index)."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Old", "artist": "Art"})

    corrections = [Divergence("A1", "title", "Old", "New")]
    apply_corrections(corrections, tracks)  # return value unused

    entry = tracks.get_by_apple_id("A1")
    assert entry is not None
    assert entry["title"] == "New"
    # save() was called at end of apply_corrections → dirty cleared
    assert not tracks._dirty


@patch(f"{_PATCH}.update_tracks_batch")
def test_apply_updates_apple_cache(mock_update, tmp_path: Path) -> None:
    """apply_corrections updates Apple cache in memory."""

    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Old"})

    apple = MagicMock()
    lib_entry = LibraryEntry(apple_id="A1", title="Old", artist="Art", album="Al")
    apple.get_all.return_value = {"A1": lib_entry}

    corrections = [Divergence("A1", "title", "Old", "New")]
    apply_corrections(corrections, tracks, apple_store=apple)

    assert lib_entry.title == "New"  # updated in-place


# ── apply_corrections: cover ──────────────────────────────────────────────


@patch(f"{_PATCH}.set_artwork_batch")
@patch(f"{_PATCH}.write_cover")
@patch("music_manager.services.resolver.download_cover_file")
def test_apply_cover_success(mock_dl, mock_write, mock_art, tmp_path: Path) -> None:
    """Cover correction: download → write_cover → set_artwork."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S", "file_path": "/music/s.m4a"})

    mock_dl.return_value = "/tmp/cover_fix.jpg"

    corrections = [Divergence("A1", "cover", "", "https://cover.jpg")]
    count, _ = apply_corrections(
        corrections,
        tracks,
        cover_url="https://cover.jpg",
        cover_entries=["A1"],
    )

    assert count == 1  # counted after successful download
    mock_write.assert_called_once()
    mock_art.assert_called_once()


@patch("music_manager.services.resolver.download_cover_file", return_value="")
def test_apply_cover_download_fails_count_zero(mock_dl, tmp_path: Path) -> None:
    """Cover download failure → count NOT incremented."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S"})

    corrections = [Divergence("A1", "cover", "", "https://bad.url")]
    count, _ = apply_corrections(
        corrections,
        tracks,
        cover_url="https://bad.url",
        cover_entries=["A1"],
    )

    assert count == 0  # NOT counted


@patch(f"{_PATCH}.set_artwork_batch")
@patch(f"{_PATCH}.write_cover")
@patch("music_manager.services.resolver.download_cover_file")
def test_apply_cover_no_file_path_skips_write(
    mock_dl,
    mock_write,
    mock_art,
    tmp_path: Path,
) -> None:
    """Track without file_path → write_cover skipped, set_artwork still called."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S", "file_path": ""})

    mock_dl.return_value = "/tmp/cover_fix.jpg"

    corrections = [Divergence("A1", "cover", "", "https://c.jpg")]
    apply_corrections(corrections, tracks, cover_url="https://c.jpg", cover_entries=["A1"])

    mock_write.assert_not_called()  # no file_path
    mock_art.assert_called_once()  # AppleScript still applies


# ── apply_corrections: mixed metadata + cover ─────────────────────────────


@patch(f"{_PATCH}.set_artwork_batch")
@patch(f"{_PATCH}.write_cover")
@patch("music_manager.services.resolver.download_cover_file")
@patch(f"{_PATCH}.update_tracks_batch")
def test_apply_mixed_corrections(
    mock_update,
    mock_dl,
    mock_write,
    mock_art,
    tmp_path: Path,
) -> None:
    """Metadata + cover corrections applied together."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Old", "file_path": "/m/s.m4a"})

    mock_dl.return_value = "/tmp/cover_fix.jpg"

    corrections = [
        Divergence("A1", "title", "Old", "New"),
        Divergence("A1", "cover", "", "https://c.jpg"),
    ]
    count, _ = apply_corrections(
        corrections,
        tracks,
        cover_url="https://c.jpg",
        cover_entries=["A1"],
    )

    assert count == 2  # 1 metadata + 1 cover
    mock_update.assert_called_once()
    mock_art.assert_called_once()


# ── save_refusals: merge behavior ─────────────────────────────────────────


def test_save_refusals_merges_existing(tmp_path: Path) -> None:
    """New refusals merge with existing ones."""

    prefs_path = str(tmp_path / "prefs.json")
    save_json(prefs_path, {"refusals": {"A1:title": "Old"}})

    save_refusals([Divergence("A2", "artist", "X", "Y")], prefs_path)

    prefs = load_json(prefs_path)
    assert prefs["refusals"]["A1:title"] == "Old"  # preserved
    assert prefs["refusals"]["A2:artist"] == "Y"  # added
