"""Tests for options/complete_albums.py — album completion logic."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from music_manager.core.config import Paths
from music_manager.core.models import PendingTrack
from music_manager.options.complete_albums import complete_album, find_incomplete_albums
from music_manager.pipeline.executor import BatchResult
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.options.complete_albums"

_ALBUM_DATA = {
    "title": "Album",
    "artist": "Artist",
    "total_tracks": 3,
    "genre": "Rock",
    "year": "2020",
    "cover_url": "https://cover.jpg",
    "album_artist": "Artist",
    "release_date": "2020-01-01",
}

_TRACKLIST = [
    {
        "id": 10,
        "title": "Track 1",
        "isrc": "ISRC1",
        "artist": {"name": "Artist"},
        "album": {"id": 1, "title": "Album"},
        "track_position": 1,
        "disk_number": 1,
        "duration": 200,
    },
    {
        "id": 20,
        "title": "Track 2",
        "isrc": "ISRC2",
        "artist": {"name": "Artist"},
        "album": {"id": 1, "title": "Album"},
        "track_position": 2,
        "disk_number": 1,
        "duration": 180,
    },
    {
        "id": 30,
        "title": "Track 3",
        "isrc": "ISRC3",
        "artist": {"name": "Artist"},
        "album": {"id": 1, "title": "Album"},
        "track_position": 3,
        "disk_number": 1,
        "duration": 220,
    },
]


# ── find_incomplete_albums ───────────────────────────────────────────────


@patch(f"{_PATCH}.get_album_tracklist")
@patch(f"{_PATCH}.fetch_album_with_cover")
def test_find_incomplete_detects_missing(mock_fetch, mock_tl, tmp_path: Path) -> None:
    """Album with fewer local tracks than total → incomplete."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Track 1", "deezer_id": 10, "album_id": 1, "isrc": "ISRC1"})

    albums = Albums(str(tmp_path / "albums.json"))
    mock_fetch.return_value = {"title": "Album", "artist": "Artist", "total_tracks": 3}
    mock_tl.return_value = [
        {"id": 10, "title": "Track 1", "isrc": "ISRC1", "artist": {"name": "Artist"}},
        {"id": 20, "title": "Track 2", "isrc": "ISRC2", "artist": {"name": "Artist"}},
        {"id": 30, "title": "Track 3", "isrc": "ISRC3", "artist": {"name": "Artist"}},
    ]

    result = find_incomplete_albums(tracks, albums)

    assert len(result) == 1
    assert result[0]["album_id"] == 1
    assert result[0]["local"] == 1
    assert result[0]["total"] == 3


@patch(f"{_PATCH}.get_album_tracklist")
@patch(f"{_PATCH}.fetch_album_with_cover")
def test_find_incomplete_skips_complete(mock_fetch, mock_tl, tmp_path: Path) -> None:
    """Album with all tracks present → not listed."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Track 1", "deezer_id": 10, "album_id": 1, "isrc": "ISRC1"})
    tracks.add("A2", {"title": "Track 2", "deezer_id": 20, "album_id": 1, "isrc": "ISRC2"})

    albums = Albums(str(tmp_path / "albums.json"))
    mock_fetch.return_value = {"title": "Album", "artist": "Artist", "total_tracks": 2}
    mock_tl.return_value = [
        {"id": 10, "title": "Track 1", "isrc": "ISRC1", "artist": {"name": "Artist"}},
        {"id": 20, "title": "Track 2", "isrc": "ISRC2", "artist": {"name": "Artist"}},
    ]

    result = find_incomplete_albums(tracks, albums)
    assert len(result) == 0


def test_find_incomplete_skips_unidentified(tmp_path: Path) -> None:
    """Tracks without deezer_id are ignored."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Track 1"})  # no deezer_id

    albums = Albums(str(tmp_path / "albums.json"))
    result = find_incomplete_albums(tracks, albums)
    assert len(result) == 0


@patch(f"{_PATCH}.get_album_tracklist")
@patch(f"{_PATCH}.fetch_album_with_cover")
def test_find_incomplete_no_total_tracks(mock_fetch, mock_tl, tmp_path: Path) -> None:
    """Album with total_tracks=0 or missing → skipped."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Track 1", "deezer_id": 10, "album_id": 1})

    albums = Albums(str(tmp_path / "albums.json"))
    mock_fetch.return_value = {"title": "Album", "artist": "Artist", "total_tracks": 0}

    result = find_incomplete_albums(tracks, albums)
    assert len(result) == 0


@patch(f"{_PATCH}.fetch_album_with_cover")
def test_find_incomplete_album_fetch_fails(mock_fetch, tmp_path: Path) -> None:
    """Album fetch returns None → skipped gracefully."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Track 1", "deezer_id": 10, "album_id": 1})

    albums = Albums(str(tmp_path / "albums.json"))
    mock_fetch.return_value = None

    result = find_incomplete_albums(tracks, albums)
    assert len(result) == 0


@patch(f"{_PATCH}.get_album_tracklist")
@patch(f"{_PATCH}.fetch_album_with_cover")
def test_find_incomplete_failed_tracks_not_counted(mock_fetch, mock_tl, tmp_path: Path) -> None:
    """Tracks with status='failed' should not count as local tracks."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add(
        "A1",
        {
            "title": "Track 1",
            "deezer_id": 10,
            "album_id": 1,
            "status": "done",
            "isrc": "ISRC1",
        },
    )
    tracks.add(
        "A2",
        {
            "title": "Track 2",
            "deezer_id": 20,
            "album_id": 1,
            "status": "failed",
            "isrc": "ISRC2",
        },
    )

    albums = Albums(str(tmp_path / "albums.json"))
    mock_fetch.return_value = {"title": "Album", "artist": "Artist", "total_tracks": 3}
    mock_tl.return_value = [
        {"id": 10, "title": "Track 1", "isrc": "ISRC1", "artist": {"name": "Artist"}},
        {"id": 20, "title": "Track 2", "isrc": "ISRC2", "artist": {"name": "Artist"}},
        {"id": 30, "title": "Track 3", "isrc": "ISRC3", "artist": {"name": "Artist"}},
    ]

    result = find_incomplete_albums(tracks, albums)
    assert len(result) == 1
    assert result[0]["local"] == 1  # failed not counted


@patch(f"{_PATCH}.get_album_tracklist")
@patch(f"{_PATCH}.fetch_album_with_cover")
def test_find_incomplete_multiple_albums(mock_fetch, mock_tl, tmp_path: Path) -> None:
    """Multiple incomplete albums detected."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "T1", "deezer_id": 10, "album_id": 1, "isrc": "I1"})
    tracks.add("A2", {"title": "T2", "deezer_id": 20, "album_id": 2, "isrc": "I2"})
    tracks.add("A3", {"title": "T3", "deezer_id": 30, "album_id": 2, "isrc": "I3"})

    albums = Albums(str(tmp_path / "albums.json"))
    mock_fetch.side_effect = [
        {"title": "Album A", "artist": "Art", "total_tracks": 3},
        {"title": "Album B", "artist": "Art", "total_tracks": 5},
    ]
    mock_tl.side_effect = [
        [
            {"id": 10, "title": "T1", "isrc": "I1", "artist": {"name": "Art"}},
            {"id": 11, "title": "T1b", "isrc": "I1b", "artist": {"name": "Art"}},
            {"id": 12, "title": "T1c", "isrc": "I1c", "artist": {"name": "Art"}},
        ],
        [
            {"id": 20, "title": "T2", "isrc": "I2", "artist": {"name": "Art"}},
            {"id": 21, "title": "T2b", "isrc": "I2b", "artist": {"name": "Art"}},
            {"id": 22, "title": "T2c", "isrc": "I2c", "artist": {"name": "Art"}},
            {"id": 23, "title": "T2d", "isrc": "I2d", "artist": {"name": "Art"}},
            {"id": 24, "title": "T2e", "isrc": "I2e", "artist": {"name": "Art"}},
        ],
    ]

    result = find_incomplete_albums(tracks, albums)
    assert len(result) == 2


# ── complete_album ───────────────────────────────────────────────────────


@patch(f"{_PATCH}.run_import_pipeline")
@patch(f"{_PATCH}.fetch_album_with_cover")
@patch(f"{_PATCH}.get_album_tracklist")
def test_complete_imports_missing_tracks(
    mock_tl, mock_album, mock_pipeline, tmp_path: Path,
) -> None:
    """Only missing tracks are imported — existing ones skipped via dedup."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Track 1", "isrc": "ISRC1", "deezer_id": 10, "status": "done"})

    albums = Albums(str(tmp_path / "albums.json"))
    paths = Paths(str(tmp_path / "data"))

    mock_tl.return_value = _TRACKLIST
    mock_album.return_value = _ALBUM_DATA
    mock_pipeline.return_value = BatchResult(imported=2)

    result = complete_album(1, paths, tracks, albums)

    assert result.tracks_imported == 2
    # Pipeline called with 2 tracks (Track 2 + Track 3, Track 1 is duplicate)
    assert mock_pipeline.call_count == 1
    call_tracks = mock_pipeline.call_args[0][0]
    assert len(call_tracks) == 2


@patch(f"{_PATCH}.run_import_pipeline")
@patch(f"{_PATCH}.fetch_album_with_cover")
@patch(f"{_PATCH}.get_album_tracklist")
def test_complete_pending_on_failure(mock_tl, mock_album, mock_pipeline, tmp_path: Path) -> None:
    """Failed imports produce PendingTrack entries."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = Paths(str(tmp_path / "data"))

    mock_tl.return_value = [_TRACKLIST[0]]
    mock_album.return_value = _ALBUM_DATA
    mock_pipeline.return_value = BatchResult(
        imported=0,
        pending=[PendingTrack(reason="youtube_failed")],
    )

    result = complete_album(1, paths, tracks, albums)

    assert result.tracks_imported == 0
    assert len(result.pending) == 1
    assert result.pending[0].reason == "youtube_failed"


@patch(f"{_PATCH}.run_import_pipeline")
@patch(f"{_PATCH}.fetch_album_with_cover")
@patch(f"{_PATCH}.get_album_tracklist")
def test_complete_all_duplicates_no_import(
    mock_tl,
    mock_album,
    mock_pipeline,
    tmp_path: Path,
) -> None:
    """All tracks already exist → no imports, pipeline not called."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Track 1", "isrc": "ISRC1", "deezer_id": 10, "status": "done"})
    tracks.add("A2", {"title": "Track 2", "isrc": "ISRC2", "deezer_id": 20, "status": "done"})
    tracks.add("A3", {"title": "Track 3", "isrc": "ISRC3", "deezer_id": 30, "status": "done"})

    albums = Albums(str(tmp_path / "albums.json"))
    paths = Paths(str(tmp_path / "data"))

    mock_tl.return_value = _TRACKLIST
    mock_album.return_value = _ALBUM_DATA

    result = complete_album(1, paths, tracks, albums)

    assert result.tracks_imported == 0
    mock_pipeline.assert_not_called()


@patch(f"{_PATCH}.fetch_album_with_cover")
@patch(f"{_PATCH}.get_album_tracklist")
def test_complete_empty_tracklist(mock_tl, mock_album, tmp_path: Path) -> None:
    """Empty tracklist → no imports, no crash."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = Paths(str(tmp_path / "data"))

    mock_tl.return_value = []
    mock_album.return_value = _ALBUM_DATA

    result = complete_album(1, paths, tracks, albums)
    assert result.tracks_imported == 0
    assert result.pending == []


@patch(f"{_PATCH}.fetch_album_with_cover")
@patch(f"{_PATCH}.get_album_tracklist")
def test_complete_album_data_none(mock_tl, mock_album, tmp_path: Path) -> None:
    """Album data None → no crash, empty result."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = Paths(str(tmp_path / "data"))

    mock_album.return_value = None
    mock_tl.return_value = None

    result = complete_album(1, paths, tracks, albums)
    assert result.tracks_imported == 0


@patch(f"{_PATCH}.run_import_pipeline")
@patch(f"{_PATCH}.fetch_album_with_cover")
@patch(f"{_PATCH}.get_album_tracklist")
def test_complete_progress_callback(mock_tl, mock_album, mock_pipeline, tmp_path: Path) -> None:
    """Progress callback is forwarded to pipeline."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = Paths(str(tmp_path / "data"))

    mock_tl.return_value = _TRACKLIST
    mock_album.return_value = _ALBUM_DATA
    mock_pipeline.return_value = BatchResult(imported=3)
    progress = MagicMock()

    result = complete_album(1, paths, tracks, albums, on_progress=progress)

    assert result.tracks_imported == 3
    # Progress callback passed to pipeline
    assert mock_pipeline.call_args[1]["on_progress"] is progress


@patch(f"{_PATCH}.run_import_pipeline")
@patch(f"{_PATCH}.fetch_album_with_cover")
@patch(f"{_PATCH}.get_album_tracklist")
def test_complete_mixed_results(mock_tl, mock_album, mock_pipeline, tmp_path: Path) -> None:
    """Mix of success and failure."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    albums = Albums(str(tmp_path / "albums.json"))
    paths = Paths(str(tmp_path / "data"))

    mock_tl.return_value = _TRACKLIST
    mock_album.return_value = _ALBUM_DATA
    mock_pipeline.return_value = BatchResult(
        imported=2,
        pending=[PendingTrack(reason="youtube_failed")],
    )

    result = complete_album(1, paths, tracks, albums)

    assert result.tracks_imported == 2
    assert len(result.pending) == 1
