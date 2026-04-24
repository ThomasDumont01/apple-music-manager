"""Exhaustive tests for identify.py — refactored album-first approach."""

from unittest.mock import patch

from music_manager.core.models import Track
from music_manager.options.identify import (
    confirm_album,
    confirm_track,
    identify_library,
)
from music_manager.pipeline.dedup import is_duplicate
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.options.identify"


# ══════════════════════════════════════════════════════════════════════════
# Phase 1: ISRC scan
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_PATCH}.scan_isrc", return_value=2)
def test_isrc_scan_only_tracks_without_isrc(mock_scan, tmp_path) -> None:
    """Only tracks without ISRC are scanned."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S1", "artist": "A", "isrc": "", "file_path": "/m/s1.m4a"})
    tracks.add("A2", {"title": "S2", "artist": "A", "isrc": "HAS", "file_path": "/m/s2.m4a"})
    albums = Albums(str(tmp_path / "a.json"))

    identify_library(tracks, albums)

    mock_scan.assert_called_once()
    scan_entries = mock_scan.call_args[0][0]
    assert "A1" in scan_entries
    assert "A2" not in scan_entries


@patch(f"{_PATCH}.scan_isrc", return_value=0)
def test_isrc_scan_skipped_if_all_have_isrc(mock_scan, tmp_path) -> None:
    """No scan if all tracks have ISRC."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S", "artist": "A", "isrc": "ISRC1"})
    albums = Albums(str(tmp_path / "a.json"))

    identify_library(tracks, albums)
    mock_scan.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════
# Phase 2: match from known albums
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_PATCH}.write_isrc")
@patch("music_manager.services.resolver.get_album_tracklist")
@patch("music_manager.services.resolver.resolve_by_id")
def test_match_known_album(mock_resolve, mock_tl, mock_write, tmp_path) -> None:
    """Tracks with known album_id are matched via tracklist."""
    tracks = Tracks(str(tmp_path / "t.json"))
    # Track already identified (provides known album)
    tracks.add(
        "A1",
        {
            "title": "Song1",
            "artist": "Art",
            "album": "MyAlbum",
            "deezer_id": 10,
            "album_id": 99,
            "isrc": "I1",
        },
    )
    # Track to identify (same album)
    tracks.add(
        "A2",
        {
            "title": "Song2",
            "artist": "Art",
            "album": "MyAlbum",
            "isrc": "I2",
        },
    )
    albums = Albums(str(tmp_path / "a.json"))

    mock_tl.return_value = [
        {"id": 20, "title": "Song2"},
    ]
    mock_resolve.return_value = Track(
        isrc="I2",
        title="Song2",
        artist="Art",
        album="MyAlbum",
        deezer_id=20,
        album_id=99,
    )

    result = identify_library(tracks, albums)

    assert result.auto_validated == 1
    entry = tracks.get_by_apple_id("A2")
    assert entry is not None
    assert entry["deezer_id"] == 20


@patch(f"{_PATCH}.write_isrc")
def test_match_known_album_soft_title(mock_write, tmp_path) -> None:
    """Soft match: 'Jam' matches 'Jam (Remastered)' via _prepare_title."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "Other",
            "artist": "Art",
            "album": "Album",
            "deezer_id": 10,
            "album_id": 99,
        },
    )
    tracks.add("A2", {"title": "Jam", "artist": "Art", "album": "Album"})
    albums = Albums(str(tmp_path / "a.json"))
    albums.put(
        99,
        {
            "title": "Album",
            "_tracklist": [
                {"id": 20, "title": "Jam (Remastered Version)"},
            ],
        },
    )

    with patch("music_manager.services.resolver.resolve_by_id") as mock_r:
        mock_r.return_value = Track(
            isrc="I2",
            title="Jam",
            artist="Art",
            album="Album",
            deezer_id=20,
            album_id=99,
        )
        result = identify_library(tracks, albums)

    assert result.auto_validated == 1


def test_skips_already_identified(tmp_path) -> None:
    """Tracks with deezer_id are not re-processed."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "S",
            "artist": "A",
            "deezer_id": 99,
            "album_id": 1,
        },
    )
    albums = Albums(str(tmp_path / "a.json"))

    result = identify_library(tracks, albums)
    assert result.auto_validated == 0
    assert len(result.albums_to_review) == 0


# ══════════════════════════════════════════════════════════════════════════
# Phase 3: group by album
# ══════════════════════════════════════════════════════════════════════════


def test_unresolved_grouped_by_album(tmp_path) -> None:
    """Unresolved tracks with same album are grouped."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S1", "artist": "Art", "album": "Al"})
    tracks.add("A2", {"title": "S2", "artist": "Art", "album": "Al"})
    tracks.add("A3", {"title": "S3", "artist": "Art", "album": "Other"})
    albums = Albums(str(tmp_path / "a.json"))

    result = identify_library(tracks, albums)

    # 2 groups: "Al" (2 tracks) + "Other" (1 track)
    assert len(result.albums_to_review) == 2
    album_names = {g["album_name"] for g in result.albums_to_review}
    assert "Al" in album_names
    assert "Other" in album_names


def test_no_album_grouped_separately(tmp_path) -> None:
    """Tracks without album get individual groups."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S1", "artist": "Art", "album": ""})
    tracks.add("A2", {"title": "S2", "artist": "Art", "album": ""})
    albums = Albums(str(tmp_path / "a.json"))

    result = identify_library(tracks, albums)

    # Each no-album track gets its own group
    assert len(result.albums_to_review) == 2


def test_empty_library(tmp_path) -> None:
    """Empty store → no work, no crash."""
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))

    result = identify_library(tracks, albums)
    assert result.auto_validated == 0
    assert len(result.albums_to_review) == 0


# ══════════════════════════════════════════════════════════════════════════
# confirm_album
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_PATCH}.write_isrc")
@patch("music_manager.services.resolver.fetch_album_with_cover", return_value={})
@patch("music_manager.services.resolver.get_album_tracklist")
@patch("music_manager.services.resolver.resolve_by_id")
def test_confirm_album_matches_all(mock_r, mock_tl, mock_alb, mock_w, tmp_path) -> None:
    """confirm_album matches tracks via tracklist and saves."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Song1", "artist": "Art", "album": "Al"})
    tracks.add("A2", {"title": "Song2", "artist": "Art", "album": "Al"})
    albums = Albums(str(tmp_path / "a.json"))

    mock_tl.return_value = [
        {"id": 10, "title": "Song1"},
        {"id": 20, "title": "Song2"},
    ]
    mock_r.side_effect = [
        Track(isrc="I1", title="Song1", artist="Art", album="Al", deezer_id=10, album_id=1),
        Track(isrc="I2", title="Song2", artist="Art", album="Al", deezer_id=20, album_id=1),
    ]

    matched, unmatched = confirm_album(1, ["A1", "A2"], tracks, albums)

    assert matched == 2
    assert unmatched == []
    a1 = tracks.get_by_apple_id("A1")
    a2 = tracks.get_by_apple_id("A2")
    assert a1 is not None
    assert a2 is not None
    assert a1["deezer_id"] == 10
    assert a2["deezer_id"] == 20


@patch("music_manager.services.resolver.fetch_album_with_cover", return_value={})
@patch("music_manager.services.resolver.get_album_tracklist")
@patch("music_manager.services.resolver.resolve_by_id")
def test_confirm_album_partial_match(mock_r, mock_tl, mock_alb, tmp_path) -> None:
    """Tracks not in tracklist → unmatched."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Known", "artist": "Art", "album": "Al"})
    tracks.add("A2", {"title": "Unknown", "artist": "Art", "album": "Al"})
    albums = Albums(str(tmp_path / "a.json"))

    mock_tl.return_value = [{"id": 10, "title": "Known"}]
    mock_r.return_value = Track(
        isrc="I1",
        title="Known",
        artist="Art",
        album="Al",
        deezer_id=10,
        album_id=1,
    )

    matched, unmatched = confirm_album(1, ["A1", "A2"], tracks, albums)

    assert matched == 1
    assert unmatched == ["A2"]


# ══════════════════════════════════════════════════════════════════════════
# confirm_track
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_PATCH}.write_isrc")
def test_confirm_track_stores_data(mock_write, tmp_path) -> None:
    """confirm_track stores deezer_id + album_id."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S", "artist": "A"})

    confirm_track(
        "A1",
        {
            "deezer_id": 42,
            "isrc": "ISRC1",
            "cover_url": "https://c.jpg",
            "album_id": 99,
        },
        tracks,
        file_path="/m/s.m4a",
    )

    entry = tracks.get_by_apple_id("A1")
    assert entry is not None
    assert entry["deezer_id"] == 42
    assert entry["album_id"] == 99


@patch(f"{_PATCH}.write_isrc")
def test_confirm_track_isrc_case(mock_write, tmp_path) -> None:
    """ISRC stored as-is, findable case-insensitive."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S", "artist": "A"})

    confirm_track("A1", {"deezer_id": 1, "isrc": "abc123", "cover_url": ""}, tracks)

    assert tracks.get_by_isrc("ABC123") is not None


# ══════════════════════════════════════════════════════════════════════════
# Data integrity
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_PATCH}.write_isrc")
@patch("music_manager.services.resolver.resolve_by_id")
def test_identify_then_dedup_works(mock_r, mock_write, tmp_path) -> None:
    """After identify from known album, dedup detects by ISRC."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "Known",
            "artist": "Art",
            "album": "Al",
            "deezer_id": 10,
            "album_id": 99,
        },
    )
    tracks.add("A2", {"title": "Song", "artist": "Art", "album": "Al", "isrc": ""})
    albums = Albums(str(tmp_path / "a.json"))
    albums.put(99, {"title": "Al", "_tracklist": [{"id": 20, "title": "Song"}]})

    mock_r.return_value = Track(
        isrc="ISRC_NEW",
        title="Song",
        artist="Art",
        album="Al",
        deezer_id=20,
        album_id=99,
    )

    identify_library(tracks, albums)

    assert is_duplicate("ISRC_NEW", "Song", "Art", tracks) is True
    assert is_duplicate("isrc_new", "Song", "Art", tracks) is True


@patch(f"{_PATCH}.write_isrc")
@patch("music_manager.services.resolver.resolve_by_id")
def test_identify_preserves_existing_fields(mock_r, mock_write, tmp_path) -> None:
    """Identify adds fields but doesn't overwrite origin/status."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "Known",
            "artist": "Art",
            "album": "Al",
            "deezer_id": 10,
            "album_id": 99,
        },
    )
    tracks.add(
        "A2",
        {
            "title": "Song",
            "artist": "Art",
            "album": "Al",
            "origin": "baseline",
            "status": None,
            "file_path": "/m/s.m4a",
        },
    )
    albums = Albums(str(tmp_path / "a.json"))
    albums.put(99, {"title": "Al", "_tracklist": [{"id": 20, "title": "Song"}]})

    mock_r.return_value = Track(
        isrc="I2",
        title="Song",
        artist="Art",
        album="Al",
        deezer_id=20,
        album_id=99,
    )

    identify_library(tracks, albums)

    entry = tracks.get_by_apple_id("A2")
    assert entry is not None
    assert entry["origin"] == "baseline"
    assert entry["status"] is None
    assert entry["deezer_id"] == 20


def test_progress_callback(tmp_path) -> None:
    """on_progress called for unidentified tracks."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S1", "artist": "A"})
    tracks.add("A2", {"title": "S2", "artist": "A"})
    tracks.add("A3", {"title": "S3", "artist": "A", "deezer_id": 99})
    albums = Albums(str(tmp_path / "a.json"))

    calls = []
    identify_library(tracks, albums, on_progress=lambda c, t: calls.append((c, t)))

    # 2 unidentified → progress called
    assert len(calls) >= 1
    assert calls[-1][1] == 2  # total = 2
