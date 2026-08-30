"""Dedup must not shield a track the user deleted from Apple Music.

``tracks.json`` outlives the library: a track removed from Apple Music is
still recorded there. Treating that record as "already imported" made the CSV
re-download queue skip every single row it was built from.
"""

from pathlib import Path
from unittest.mock import patch

from music_manager.core.config import Paths
from music_manager.core.io import save_csv
from music_manager.options.import_tracks import _live_apple_ids, process_csv
from music_manager.pipeline.dedup import is_duplicate
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

_ENTRY = {
    "apple_id": "AP_GONE",
    "title": "Papaoutai",
    "artist": "Stromae",
    "album": "Racine carrée",
    "isrc": "BET671300160",
    "deezer_id": 1234,
    "status": "done",
}


def _store(tmp_path: Path) -> Tracks:
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("AP_GONE", dict(_ENTRY))
    return store


# ── is_duplicate ───────────────────────────────────────────────────────────


def test_store_only_behaviour_is_unchanged(tmp_path: Path) -> None:
    """Without a library snapshot, the store stays the sole authority."""
    assert is_duplicate("BET671300160", "Papaoutai", "Stromae", _store(tmp_path)) is True


def test_live_track_is_still_a_duplicate(tmp_path: Path) -> None:
    assert (
        is_duplicate("BET671300160", "Papaoutai", "Stromae", _store(tmp_path), {"AP_GONE"}) is True
    )


def test_deleted_track_is_not_a_duplicate(tmp_path: Path) -> None:
    """The whole point: it's gone from Apple Music, so let it be re-imported."""
    assert (
        is_duplicate("BET671300160", "Papaoutai", "Stromae", _store(tmp_path), {"AP_OTHER"})
        is False
    )


def test_title_artist_match_also_honours_the_library(tmp_path: Path) -> None:
    """A row without ISRC must get the same treatment as one with."""
    assert is_duplicate("", "Papaoutai", "Stromae", _store(tmp_path), {"AP_OTHER"}) is False
    assert is_duplicate("", "Papaoutai", "Stromae", _store(tmp_path), {"AP_GONE"}) is True


def test_entry_without_apple_id_is_not_a_duplicate(tmp_path: Path) -> None:
    """No apple_id means nothing to play in Apple Music."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("KEY", {**_ENTRY, "apple_id": ""})
    assert is_duplicate("BET671300160", "Papaoutai", "Stromae", store, {"AP_GONE"}) is False


# ── process_csv ────────────────────────────────────────────────────────────


def test_csv_reimports_a_track_missing_from_the_library(tmp_path: Path) -> None:
    """End-to-end: the requetes.csv restore queue actually imports.

    Regression: every row was skipped as a duplicate because the store still
    remembered the track, so the queue imported nothing at all.
    """
    csv_path = str(tmp_path / "requetes.csv")
    save_csv(
        csv_path,
        [
            {
                "title": "Papaoutai",
                "artist": "Stromae",
                "album": "Racine carrée",
                "isrc": "BET671300160",
            }
        ],
    )
    paths = Paths(str(tmp_path / "data"))
    tracks = _store(tmp_path)
    albums = Albums(str(tmp_path / "albums.json"))

    with (
        patch(
            "music_manager.options.import_tracks._live_apple_ids",
            return_value={"AP_SOMETHING_ELSE"},
        ),
        patch("music_manager.options.import_tracks.resolve") as mock_resolve,
        patch("music_manager.options.import_tracks.import_resolved_track") as mock_import,
        patch("music_manager.options.import_tracks.cleanup_covers"),
    ):
        mock_resolve.return_value.status = "resolved"
        mock_resolve.return_value.track = type(
            "T", (), {"isrc": "BET671300160", "title": "Papaoutai", "apple_id": "AP_NEW"}
        )()
        mock_import.return_value = None
        result = process_csv(csv_path, paths, tracks, albums)

    assert result.skipped == 0
    assert result.imported == 1
    mock_import.assert_called_once()


def test_csv_still_skips_a_track_that_is_present(tmp_path: Path) -> None:
    """No pointless re-download when the track is genuinely there."""
    csv_path = str(tmp_path / "requetes.csv")
    save_csv(
        csv_path,
        [{"title": "Papaoutai", "artist": "Stromae", "album": "", "isrc": "BET671300160"}],
    )
    paths = Paths(str(tmp_path / "data"))
    tracks = _store(tmp_path)
    albums = Albums(str(tmp_path / "albums.json"))

    with (
        patch("music_manager.options.import_tracks._live_apple_ids", return_value={"AP_GONE"}),
        patch("music_manager.options.import_tracks.import_resolved_track") as mock_import,
        patch("music_manager.options.import_tracks.cleanup_covers"),
    ):
        result = process_csv(csv_path, paths, tracks, albums)

    assert result.skipped == 1
    mock_import.assert_not_called()


def test_unreadable_library_falls_back_to_the_store(tmp_path: Path) -> None:
    """Apple Music unavailable must not trigger a re-download of everything."""
    csv_path = str(tmp_path / "requetes.csv")
    save_csv(
        csv_path,
        [{"title": "Papaoutai", "artist": "Stromae", "album": "", "isrc": "BET671300160"}],
    )
    paths = Paths(str(tmp_path / "data"))
    tracks = _store(tmp_path)
    albums = Albums(str(tmp_path / "albums.json"))

    with (
        patch("music_manager.options.import_tracks._live_apple_ids", return_value=None),
        patch("music_manager.options.import_tracks.import_resolved_track") as mock_import,
        patch("music_manager.options.import_tracks.cleanup_covers"),
    ):
        result = process_csv(csv_path, paths, tracks, albums)

    assert result.skipped == 1
    mock_import.assert_not_called()


def test_live_apple_ids_returns_none_when_scan_fails() -> None:
    """A crashing scan is reported as "unknown", never as an empty library."""
    assert _live_apple_ids() is None
