"""Exhaustive import_tracks tests — playlist, callbacks, edge cases."""

import os
from pathlib import Path
from unittest.mock import patch

from music_manager.core.config import Paths
from music_manager.core.io import load_csv, save_csv
from music_manager.core.models import Track
from music_manager.options.import_tracks import find_apple_id, process_csv, remove_failed
from music_manager.services.albums import Albums
from music_manager.services.resolver import ResolveResult
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.options.import_tracks"


def _paths(tmp_path: Path, playlists: bool = False) -> Paths:
    paths = Paths(str(tmp_path / "data"))
    if playlists:
        os.makedirs(paths.playlists_dir, exist_ok=True)
    return paths


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


# ── Playlist mode ─────────────────────────────────────────────────────────


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.add_to_playlist", return_value=1)
@patch(f"{_PATCH}.import_resolved_track", return_value=None)
@patch(f"{_PATCH}.resolve")
def test_playlist_rows_not_removed(mock_resolve, mock_import, mock_pl, mock_log, tmp_path) -> None:
    """Playlist CSV: rows are NEVER removed after import."""
    paths = _paths(tmp_path, playlists=True)
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))

    csv_path = str(Path(paths.playlists_dir) / "sad.csv")
    save_csv(csv_path, [{"title": "Song", "artist": "Art", "album": "Al"}])

    track = _track()
    track.apple_id = "AP1"
    mock_resolve.return_value = ResolveResult("resolved", track=track)

    result = process_csv(csv_path, paths, tracks, albums)

    assert result.imported == 1
    remaining = load_csv(csv_path)
    assert len(remaining) == 1  # NOT removed


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.add_to_playlist", return_value=0)
@patch(f"{_PATCH}.resolve")
def test_playlist_skipped_track_collected_for_sync(
    mock_resolve, mock_pl, mock_log, tmp_path
) -> None:
    """Playlist: skipped track's apple_id is collected for playlist sync."""
    paths = _paths(tmp_path, playlists=True)
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "AP1",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "ISRC1",
            "status": "done",
            "deezer_id": 1,
            "apple_id": "AP1",
        },
    )
    albums = Albums(str(tmp_path / "albums.json"))

    csv_path = str(Path(paths.playlists_dir) / "chill.csv")
    save_csv(csv_path, [{"title": "Song", "artist": "Art", "album": "", "isrc": "ISRC1"}])

    process_csv(csv_path, paths, tracks, albums)

    # add_to_playlist should have been called with the apple_id
    mock_pl.assert_called_once()
    playlist_ids = mock_pl.call_args[0][1]
    assert "AP1" in playlist_ids


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.resolve")
def test_playlist_skipped_no_apple_id_not_in_list(mock_resolve, mock_log, tmp_path) -> None:
    """Playlist: skipped track without apple_id is NOT added to playlist."""
    paths = _paths(tmp_path, playlists=True)
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "AP1",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "ISRC1",
            "status": "done",
            "deezer_id": 1,
            # No apple_id field in the entry
        },
    )
    albums = Albums(str(tmp_path / "albums.json"))

    csv_path = str(Path(paths.playlists_dir) / "test.csv")
    save_csv(csv_path, [{"title": "Song", "artist": "Art", "album": "", "isrc": "ISRC1"}])

    result = process_csv(csv_path, paths, tracks, albums)
    # No crash, skipped
    assert result.skipped == 1


# ── on_row callback ──────────────────────────────────────────────────────


@patch(f"{_PATCH}.log_event")
@patch(f"{_PATCH}.import_resolved_track", return_value=None)
@patch(f"{_PATCH}.resolve")
def test_on_row_callback_order(mock_resolve, mock_import, mock_log, tmp_path) -> None:
    """on_row called with correct idx, total, and status."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "X",
        {
            "title": "Dup",
            "artist": "Art",
            "isrc": "I1",
            "status": "done",
            "deezer_id": 1,
        },
    )
    albums = Albums(str(tmp_path / "albums.json"))
    paths = _paths(tmp_path)

    csv_path = str(tmp_path / "import.csv")
    save_csv(
        csv_path,
        [
            {"title": "Dup", "artist": "Art", "album": "", "isrc": "I1"},
            {"title": "New", "artist": "Art2", "album": "Al"},
        ],
    )
    mock_resolve.return_value = ResolveResult("resolved", track=_track())

    calls = []

    def on_row(idx, total, title, artist, status):
        calls.append((idx, total, title, status))

    process_csv(csv_path, paths, tracks, albums, on_row=on_row)

    assert len(calls) == 2
    assert calls[0] == (0, 2, "Dup", "skipped")
    assert calls[1] == (1, 2, "New", "done")


# ── remove_failed ────────────────────────────────────────────────────────


def testremove_failed_by_isrc(tmp_path: Path) -> None:
    """Failed entry removed by ISRC match."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "OLD",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "ISRC1",
            "status": "failed",
            "deezer_id": 1,
            "apple_id": "OLD",
        },
    )
    remove_failed("ISRC1", "Song", "Art", store)
    assert store.get_by_apple_id("OLD") is None


def testremove_failed_by_title_artist(tmp_path: Path) -> None:
    """Failed entry removed by title+artist when no ISRC."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "OLD",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "",
            "status": "failed",
            "deezer_id": 1,
            "apple_id": "OLD",
        },
    )
    remove_failed("", "Song", "Art", store)
    assert store.get_by_apple_id("OLD") is None


def testremove_failed_with_empty_apple_id(tmp_path: Path) -> None:
    """Failed entry with empty apple_id still gets removed."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "KEY1",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "ISRC1",
            "status": "failed",
            "deezer_id": 1,
            "apple_id": "",
        },
    )
    remove_failed("ISRC1", "Song", "Art", store)
    # The entry should be removed via its dict key, not apple_id field
    assert len(store.all()) == 0


def testremove_failed_does_not_remove_done(tmp_path: Path) -> None:
    """Done entries are not removed."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "ISRC1",
            "status": "done",
            "deezer_id": 1,
            "apple_id": "A1",
        },
    )
    remove_failed("ISRC1", "Song", "Art", store)
    assert store.get_by_apple_id("A1") is not None


# ── find_apple_id edge cases ─────────────────────────────────────────────


def testfind_apple_id_csv_title_match(tmp_path: Path) -> None:
    """Find via csv_title soft fallback."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "AP1",
        {
            "title": "Deezer Title",
            "artist": "Art",
            "csv_title": "My Song",
            "csv_artist": "Art",
            "isrc": "",
            "apple_id": "AP1",
        },
    )
    result = find_apple_id("", "My Song", "Art", store)
    assert result == "AP1"


def testfind_apple_id_soft_first_artist(tmp_path: Path) -> None:
    """Find via prepare_title + first_artist fallback."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "AP1",
        {
            "title": "Song (Live)",
            "artist": "Queen, Adam Lambert",
            "isrc": "",
            "apple_id": "AP1",
        },
    )
    result = find_apple_id("", "Song", "Queen", store)
    assert result == "AP1"
