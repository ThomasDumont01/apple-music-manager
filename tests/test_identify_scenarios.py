"""Identify scenarios — edge cases found by audit."""

import json
from unittest.mock import patch

from music_manager.core.models import Track
from music_manager.options.identify import (
    confirm_album,
    confirm_track,
    identify_library,
)
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.options.identify"
_PATCH_R = "music_manager.services.resolver"


# ── ignored_tracks filtering ─────────────────────────────────────────────


def test_identify_respects_ignored_tracks(tmp_path) -> None:
    """Ignored tracks are not processed."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "SkipMe", "artist": "Art", "album": "Al"})
    tracks.add("A2", {"title": "Keep", "artist": "Art", "album": "Al"})
    albums = Albums(str(tmp_path / "a.json"))

    prefs_path = str(tmp_path / "prefs.json")
    with open(prefs_path, "w") as f:
        json.dump({"ignored_tracks": ["SkipMe::Art"]}, f)

    result = identify_library(tracks, albums, preferences_path=prefs_path)

    # A1 skipped, only A2 in review
    all_ids = []
    for g in result.albums_to_review:
        all_ids.extend(g["apple_ids"])
    assert "A1" not in all_ids
    assert "A2" in all_ids


# ── confirm_album edge cases ─────────────────────────────────────────────


@patch(f"{_PATCH_R}.fetch_album_with_cover", return_value={})
@patch(f"{_PATCH_R}.get_album_tracklist", return_value=None)
def test_confirm_album_empty_tracklist(mock_tl, mock_fetch, tmp_path) -> None:
    """Empty tracklist → returns all as unmatched, still saves."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S", "artist": "A"})
    albums = Albums(str(tmp_path / "a.json"))

    matched, unmatched = confirm_album(1, ["A1"], tracks, albums)

    assert matched == 0
    assert unmatched == ["A1"]


@patch(f"{_PATCH_R}.fetch_album_with_cover", return_value={})
@patch(f"{_PATCH_R}.get_album_tracklist")
@patch(f"{_PATCH_R}.resolve_by_id")
def test_confirm_album_soft_title_match(mock_r, mock_tl, mock_fetch, tmp_path) -> None:
    """'Song' matches 'Song (Remastered)' via _prepare_title."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Song", "artist": "A", "album": "Al"})
    albums = Albums(str(tmp_path / "a.json"))

    mock_tl.return_value = [{"id": 20, "title": "Song (Remastered Version)"}]
    mock_r.return_value = Track(
        isrc="I1",
        title="Song",
        artist="A",
        album="Al",
        deezer_id=20,
        album_id=1,
    )

    matched, unmatched = confirm_album(1, ["A1"], tracks, albums)

    assert matched == 1
    assert unmatched == []
    a1 = tracks.get_by_apple_id("A1")
    assert a1 is not None
    assert a1["deezer_id"] == 20


# ── confirm_track fallback ───────────────────────────────────────────────


@patch(f"{_PATCH}.write_isrc")
def test_confirm_track_no_albums_store(mock_write, tmp_path) -> None:
    """confirm_track with albums_store=None uses raw dict."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S", "artist": "A"})

    confirm_track(
        "A1",
        {"id": 42, "isrc": "ISRC1", "album_id": 99, "cover_url": ""},
        tracks,
        albums_store=None,
        file_path="/m/s.m4a",
    )

    entry = tracks.get_by_apple_id("A1")
    assert entry is not None
    assert entry["deezer_id"] == 42
    assert entry["album_id"] == 99


# ── Special characters ────────────────────────────────────────────────────


def test_group_by_album_special_chars(tmp_path) -> None:
    """Albums with special chars grouped correctly."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "S1", "artist": "A", "album": "L'Album"})
    tracks.add("A2", {"title": "S2", "artist": "A", "album": "L'Album"})
    tracks.add("A3", {"title": "S3", "artist": "A", "album": "Other™"})
    albums = Albums(str(tmp_path / "a.json"))

    result = identify_library(tracks, albums)

    assert len(result.albums_to_review) == 2


def test_no_isrc_no_album(tmp_path) -> None:
    """Track without ISRC and without album gets its own group."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Solo", "artist": "A"})
    albums = Albums(str(tmp_path / "a.json"))

    result = identify_library(tracks, albums)

    assert len(result.albums_to_review) == 1
    assert "A1" in result.albums_to_review[0]["apple_ids"]


# ── Progress callback edge case ──────────────────────────────────────────


def test_progress_empty_library(tmp_path) -> None:
    """on_progress with empty library doesn't crash."""
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))

    calls = []
    identify_library(
        tracks,
        albums,
        on_progress=lambda c, t: calls.append((c, t)),
    )

    # No tracks → progress called with (0, 0) or not at all
    # Should not crash


# ── Re-run identify ──────────────────────────────────────────────────────


def test_already_identified_skipped_on_rerun(tmp_path) -> None:
    """Re-running identify: already identified tracks are skipped."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "Done",
            "artist": "A",
            "album": "Al",
            "deezer_id": 10,
            "album_id": 99,
        },
    )
    albums = Albums(str(tmp_path / "a.json"))

    result = identify_library(tracks, albums)

    assert result.auto_validated == 0
    assert len(result.albums_to_review) == 0


@patch(f"{_PATCH}.write_isrc")
@patch(f"{_PATCH_R}.get_album_tracklist")
@patch(f"{_PATCH_R}.resolve_by_id")
def test_rerun_after_confirm_skips_confirmed(
    mock_r,
    mock_tl,
    mock_write,
    tmp_path,
) -> None:
    """After confirming one track, re-run skips it."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "Known",
            "artist": "A",
            "album": "Al",
            "deezer_id": 10,
            "album_id": 99,
        },
    )
    tracks.add("A2", {"title": "New", "artist": "A", "album": "Al"})
    albums = Albums(str(tmp_path / "a.json"))
    albums.put(99, {"title": "Al", "_tracklist": [{"id": 20, "title": "New"}]})

    mock_tl.return_value = [{"id": 20, "title": "New"}]
    mock_r.return_value = Track(
        isrc="I2",
        title="New",
        artist="A",
        album="Al",
        deezer_id=20,
        album_id=99,
    )

    # First run: A2 auto-validated from known album
    r1 = identify_library(tracks, albums)
    assert r1.auto_validated == 1

    # Second run: both have deezer_id → nothing to do
    r2 = identify_library(tracks, albums)
    assert r2.auto_validated == 0
    assert len(r2.albums_to_review) == 0


# ── Known album tracklist empty ──────────────────────────────────────────


@patch(f"{_PATCH_R}.get_album_tracklist", return_value=None)
def test_known_album_empty_tracklist_goes_to_review(mock_tl, tmp_path) -> None:
    """Known album but empty tracklist → track goes to review."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "Other",
            "artist": "A",
            "album": "Al",
            "deezer_id": 10,
            "album_id": 99,
        },
    )
    tracks.add("A2", {"title": "Song", "artist": "A", "album": "Al"})
    albums = Albums(str(tmp_path / "a.json"))

    result = identify_library(tracks, albums)

    assert len(result.albums_to_review) == 1
    assert "A2" in result.albums_to_review[0]["apple_ids"]
