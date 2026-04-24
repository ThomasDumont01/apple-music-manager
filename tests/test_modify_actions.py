"""Tests for modify_track.py — action functions (change_edition, redownload, etc.)."""

from pathlib import Path
from unittest.mock import patch

from music_manager.core.config import Paths
from music_manager.core.models import PendingTrack, Track
from music_manager.options.modify_track import (
    TrackMatch,
    change_album_edition,
    change_edition,
    redownload_audio,
    replace_audio_url,
)
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

_PATCH_MOD = "music_manager.options.modify_track"
_PATCH_IMP = "music_manager.pipeline.importer"
_PATCH_RES = "music_manager.services.resolver"
_PATCH_APL = "music_manager.services.apple"


def _paths(tmp_path: Path) -> Paths:
    return Paths(str(tmp_path / "data"))


# ── change_edition ────────────────────────────────────────────────────────


@patch(f"{_PATCH_IMP}.cleanup_covers")
@patch(f"{_PATCH_APL}.delete_tracks")
@patch(f"{_PATCH_IMP}.import_resolved_track", return_value=None)
@patch(f"{_PATCH_RES}.resolve_by_id")
def test_change_edition_success(mock_resolve, mock_import, mock_del, mock_clean, tmp_path) -> None:
    """Successful edition change: import new, delete old."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "OLD",
        {
            "title": "Song",
            "artist": "Art",
            "album": "Al",
            "isrc": "I1",
            "status": "done",
        },
    )
    albums = Albums(str(tmp_path / "a.json"))

    new_track = Track(isrc="I2", title="Song", artist="Art", album="Al")
    new_track.apple_id = "NEW"
    mock_resolve.return_value = new_track

    result = change_edition("OLD", 42, _paths(tmp_path), tracks, albums)

    assert result.success is True
    mock_del.assert_called_once_with(["OLD"])
    assert tracks.get_by_apple_id("OLD") is None


@patch(f"{_PATCH_RES}.resolve_by_id", return_value=None)
def test_change_edition_resolve_fails(mock_resolve, tmp_path) -> None:
    """resolve_by_id returns None → error, no deletion."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("OLD", {"title": "Song", "artist": "Art", "album": "Al", "isrc": "I1"})
    albums = Albums(str(tmp_path / "a.json"))

    result = change_edition("OLD", 42, _paths(tmp_path), tracks, albums)

    assert result.error == "deezer_resolve_failed"
    assert tracks.get_by_apple_id("OLD") is not None  # NOT deleted


@patch(f"{_PATCH_IMP}.import_resolved_track")
@patch(f"{_PATCH_RES}.resolve_by_id")
def test_change_edition_import_fails_no_delete(mock_resolve, mock_import, tmp_path) -> None:
    """Import failure → error, old track preserved."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("OLD", {"title": "Song", "artist": "Art", "album": "Al", "isrc": "I1"})
    albums = Albums(str(tmp_path / "a.json"))

    mock_resolve.return_value = Track(isrc="I2", title="S", artist="A", album="Al")
    mock_import.return_value = PendingTrack(reason="youtube_failed")

    result = change_edition("OLD", 42, _paths(tmp_path), tracks, albums)

    assert result.error == "youtube_failed"
    assert tracks.get_by_apple_id("OLD") is not None  # preserved


@patch(f"{_PATCH_IMP}.cleanup_covers")
@patch(f"{_PATCH_IMP}.import_resolved_track", return_value=None)
@patch(f"{_PATCH_RES}.resolve_by_id")
def test_change_edition_same_apple_id_no_delete(
    mock_resolve,
    mock_import,
    mock_clean,
    tmp_path,
) -> None:
    """Apple returns same ID → no deletion."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("SAME", {"title": "Song", "artist": "Art", "album": "Al", "isrc": "I1"})
    albums = Albums(str(tmp_path / "a.json"))

    new_track = Track(isrc="I2", title="S", artist="A", album="Al")
    new_track.apple_id = "SAME"  # same as old
    mock_resolve.return_value = new_track

    result = change_edition("SAME", 42, _paths(tmp_path), tracks, albums)

    assert result.success is True
    assert tracks.get_by_apple_id("SAME") is not None  # still there


# ── redownload_audio ──────────────────────────────────────────────────────


def test_redownload_not_found(tmp_path) -> None:
    """Track not in store → error."""
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))

    result = redownload_audio("MISSING", tracks, albums, _paths(tmp_path))
    assert result.error == "track_not_found"


def test_redownload_no_isrc(tmp_path) -> None:
    """Track without ISRC → error."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Song", "artist": "Art", "album": "Al", "isrc": ""})
    albums = Albums(str(tmp_path / "a.json"))

    result = redownload_audio("A1", tracks, albums, _paths(tmp_path))
    assert result.error == "no_isrc"


@patch(f"{_PATCH_IMP}.cleanup_covers")
@patch(f"{_PATCH_APL}.import_file", return_value="NEW")
@patch(f"{_PATCH_APL}.delete_tracks")
@patch(f"{_PATCH_IMP}.tag_audio_file")
@patch("music_manager.services.youtube.download_track", return_value=("/tmp/x.m4a", 200))
@patch("music_manager.services.youtube.search_by_isrc", return_value=[{"url": "u"}])
@patch(f"{_PATCH_IMP}.download_cover", return_value="")
def test_redownload_with_complete_data(m1, m2, m3, m4, m5, m6, m7, tmp_path) -> None:
    """Track with deezer_id+album_id → no Deezer API call, uses stored data."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "OLD",
        {
            "title": "Song",
            "artist": "Art",
            "album": "Al",
            "isrc": "ISRC1",
            "deezer_id": 42,
            "album_id": 10,
        },
    )
    albums = Albums(str(tmp_path / "a.json"))

    result = redownload_audio("OLD", tracks, albums, _paths(tmp_path))

    assert result.success is True
    assert tracks.get_by_apple_id("NEW") is not None


@patch(f"{_PATCH_IMP}.cleanup_covers")
@patch(f"{_PATCH_APL}.import_file", return_value="NEW")
@patch(f"{_PATCH_APL}.delete_tracks")
@patch(f"{_PATCH_IMP}.tag_audio_file")
@patch("music_manager.services.youtube.download_track", return_value=("/tmp/x.m4a", 200))
@patch("music_manager.services.youtube.search_by_isrc", return_value=[{"url": "u"}])
@patch(f"{_PATCH_RES}.fetch_album_with_cover", return_value={"title": "Al", "cover_url": ""})
@patch(f"{_PATCH_RES}.build_track")
@patch(f"{_PATCH_RES}.deezer_get")
def test_redownload_baseline_resolves_from_isrc(
    mock_dz, mock_build, mock_album, m1, m2, m3, m4, m5, m6, tmp_path
) -> None:
    """Baseline track (no deezer_id) → resolves from ISRC via Deezer."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("OLD", {"title": "Song", "artist": "Art", "isrc": "ISRC1"})
    albums = Albums(str(tmp_path / "a.json"))

    mock_dz.return_value = {
        "id": 99,
        "album": {"id": 5},
        "isrc": "ISRC1",
        "title": "Song",
        "artist": {"name": "Art"},
    }
    mock_build.return_value = Track(isrc="ISRC1", title="Song", artist="Art", album="Al")

    result = redownload_audio("OLD", tracks, albums, _paths(tmp_path))

    assert result.success is True
    mock_dz.assert_called_once_with("/track/isrc:ISRC1")


@patch("music_manager.services.youtube.search_by_isrc", return_value=[])
def test_redownload_youtube_fails(mock_search, tmp_path) -> None:
    """YouTube returns no results → error, track preserved."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "Song",
            "artist": "Art",
            "album": "Al",
            "isrc": "ISRC1",
            "deezer_id": 1,
            "album_id": 1,
        },
    )
    albums = Albums(str(tmp_path / "a.json"))

    result = redownload_audio("A1", tracks, albums, _paths(tmp_path))

    assert result.error == "youtube_failed"
    assert tracks.get_by_apple_id("A1") is not None  # preserved


# ── replace_audio_url ─────────────────────────────────────────────────────


def test_replace_url_not_found(tmp_path) -> None:
    """Track not in store → error."""
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))

    result = replace_audio_url("MISS", "https://yt/x", tracks, albums, _paths(tmp_path))
    assert result.error == "track_not_found"


def test_replace_url_no_deezer_no_isrc(tmp_path) -> None:
    """No deezer_id and no ISRC → error."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Song", "artist": "Art", "album": "Al", "isrc": "", "deezer_id": 0})
    albums = Albums(str(tmp_path / "a.json"))

    result = replace_audio_url("A1", "https://yt/x", tracks, albums, _paths(tmp_path))
    assert result.error == "deezer_resolve_failed"


@patch(f"{_PATCH_APL}.import_file", return_value="NEW")
@patch(f"{_PATCH_APL}.delete_tracks")
@patch(f"{_PATCH_IMP}.tag_audio_file")
@patch(f"{_PATCH_RES}.download_cover_file", return_value="")
@patch("music_manager.services.youtube.download_track", return_value=("/tmp/x.m4a", 200))
@patch(f"{_PATCH_RES}.resolve_by_id")
def test_replace_url_success(mock_resolve, m1, m2, m3, m4, m5, tmp_path) -> None:
    """Successful URL replace: download, tag, import, delete old."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "OLD",
        {
            "title": "S",
            "artist": "A",
            "album": "Al",
            "isrc": "I1",
            "deezer_id": 1,
            "album_id": 1,
        },
    )
    albums = Albums(str(tmp_path / "a.json"))

    mock_resolve.return_value = Track(isrc="I1", title="S", artist="A", album="Al", album_id=1)

    result = replace_audio_url("OLD", "https://yt/x", tracks, albums, _paths(tmp_path))

    assert result.success is True
    m4.assert_called_once_with(["OLD"])  # delete_tracks


@patch("music_manager.services.youtube.download_track", side_effect=RuntimeError("fail"))
@patch(f"{_PATCH_RES}.resolve_by_id")
def test_replace_url_download_fails(mock_resolve, mock_dl, tmp_path) -> None:
    """YouTube download failure → error, track preserved."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "S",
            "artist": "A",
            "album": "Al",
            "isrc": "I1",
            "deezer_id": 1,
            "album_id": 1,
        },
    )
    albums = Albums(str(tmp_path / "a.json"))
    mock_resolve.return_value = Track(isrc="I1", title="S", artist="A", album="Al")

    result = replace_audio_url("A1", "https://yt/x", tracks, albums, _paths(tmp_path))

    assert result.error == "youtube_download_failed"
    assert tracks.get_by_apple_id("A1") is not None  # preserved


# ── change_album_edition ──────────────────────────────────────────────────


@patch(f"{_PATCH_IMP}.cleanup_covers")
@patch(f"{_PATCH_IMP}.import_resolved_track", return_value=None)
@patch(f"{_PATCH_RES}.build_track")
@patch(f"{_PATCH_RES}.fetch_album_with_cover", return_value={"title": "Al"})
@patch(f"{_PATCH_RES}.deezer_get")
@patch(f"{_PATCH_RES}.get_album_tracklist")
@patch(f"{_PATCH_APL}.delete_tracks")
def test_change_album_edition_success(
    mock_del, mock_tl, mock_dz, mock_album, mock_build, mock_import, mock_clean, tmp_path
) -> None:
    """Album edition change: matched tracks re-imported, old deleted."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Song1", "artist": "Art", "album": "Al", "isrc": "OLD1"})
    albums = Albums(str(tmp_path / "a.json"))

    mock_tl.return_value = [{"id": 99, "title": "Song1"}]
    mock_dz.return_value = {
        "id": 99,
        "isrc": "NEW1",
        "title": "Song1",
        "artist": {"name": "Art"},
        "album": {"id": 2},
    }
    new_track = Track(isrc="NEW1", title="Song1", artist="Art", album="Al")
    new_track.apple_id = "B1"
    mock_build.return_value = new_track

    album_tracks = [
        TrackMatch(
            apple_id="A1", title="Song1", artist="Art", album="Al", isrc="OLD1", deezer_id=0
        )
    ]

    result = change_album_edition(album_tracks, 2, _paths(tmp_path), tracks, albums)

    assert result.success is True
    mock_del.assert_called_once_with(["A1"])


@patch(f"{_PATCH_RES}.get_album_tracklist", return_value=[])
def test_change_album_edition_empty_tracklist(mock_tl, tmp_path) -> None:
    """Empty tracklist → error."""
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))

    result = change_album_edition([], 99, _paths(tmp_path), tracks, albums)
    assert result.error == "album_tracklist_empty"


@patch(f"{_PATCH_IMP}.cleanup_covers")
@patch(f"{_PATCH_RES}.deezer_get")
@patch(f"{_PATCH_RES}.get_album_tracklist")
def test_change_album_edition_all_same_isrc(mock_tl, mock_dz, mock_clean, tmp_path) -> None:
    """All tracks already have correct ISRC → success (metadata updated)."""
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))

    mock_tl.return_value = [{"id": 99, "title": "Song1"}]
    mock_dz.return_value = {"id": 99, "isrc": "SAME1", "title": "Song1", "artist": {"name": "Art"}}

    album_tracks = [
        TrackMatch(
            apple_id="A1", title="Song1", artist="Art", album="Al", isrc="SAME1", deezer_id=0
        )
    ]

    result = change_album_edition(album_tracks, 2, _paths(tmp_path), tracks, albums)
    assert result.success is True
