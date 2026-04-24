"""Tests for options/identify.py — confirm_track + confirm_album."""

from pathlib import Path
from unittest.mock import patch

from music_manager.options.identify import _find_in_tracklist, confirm_track
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.options.identify"


# ── confirm_track ──────────────────────────────────────────────────────


@patch(f"{_PATCH}.write_isrc")
def test_confirm_track_updates_store(mock_write, tmp_path: Path) -> None:
    """confirm_track writes deezer_id, isrc, cover_url to tracks store."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Song", "artist": "Artist"})

    confirm_track(
        "A1",
        {"deezer_id": 42, "isrc": "ISRC999", "cover_url": "https://c.jpg"},
        tracks,
        file_path="/path.m4a",
    )

    entry = tracks.get_by_apple_id("A1")
    assert entry is not None
    assert entry["deezer_id"] == 42
    assert entry["isrc"] == "ISRC999"
    mock_write.assert_called_once_with("/path.m4a", "ISRC999")


@patch(f"{_PATCH}.write_isrc")
def test_confirm_track_no_isrc_no_write(mock_write, tmp_path: Path) -> None:
    """confirm_track does not write ISRC to file if empty."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Song", "artist": "Artist"})

    confirm_track(
        "A1",
        {"deezer_id": 42, "isrc": "", "cover_url": ""},
        tracks,
        file_path="/path.m4a",
    )

    mock_write.assert_not_called()


# ── _find_in_tracklist — fuzzy matching ──────────────────────────────────


def test_find_exact_normalize() -> None:
    """Pass 1: exact normalized match."""
    tracklist = [{"id": 1, "title": "Bohemian Rhapsody"}]
    assert _find_in_tracklist("bohemian rhapsody", tracklist) == tracklist[0]


def test_findprepare_title() -> None:
    """Pass 2: prepare_title strips parens."""
    tracklist = [{"id": 1, "title": "Song (Remastered 2011)"}]
    assert _find_in_tracklist("Song", tracklist) == tracklist[0]


def test_find_fuzzy_match() -> None:
    """Pass 3: fuzzy fallback catches near-matches."""
    tracklist = [{"id": 1, "title": "Til I Collapse"}]
    result = _find_in_tracklist("Till I Collapse", tracklist)
    assert result == tracklist[0]


def test_find_fuzzy_apostrophe() -> None:
    """Fuzzy handles apostrophe/punctuation differences."""
    tracklist = [{"id": 1, "title": "Cleanin' Out My Closet"}]
    result = _find_in_tracklist("Cleanin Out My Closet", tracklist)
    assert result == tracklist[0]


def test_find_fuzzy_no_false_positive() -> None:
    """Fuzzy does NOT match completely different titles."""
    tracklist = [{"id": 1, "title": "Completely Different Song"}]
    assert _find_in_tracklist("My Track Here", tracklist) is None


def test_find_fuzzy_best_score() -> None:
    """When multiple fuzzy candidates, returns highest score."""
    tracklist = [
        {"id": 1, "title": "Lose Yourself"},
        {"id": 2, "title": "Song Part 2"},
    ]
    result = _find_in_tracklist("Song Pt. 2", tracklist)
    assert result is not None
    assert result["id"] == 2  # closer match


def test_find_exact_preferred_over_fuzzy() -> None:
    """Exact match in Pass 1/2 chosen before fuzzy Pass 3."""
    tracklist = [
        {"id": 1, "title": "Song"},
        {"id": 2, "title": "Song Extended"},
    ]
    result = _find_in_tracklist("Song", tracklist)
    assert result is not None
    assert result["id"] == 1  # exact match, not fuzzy


def test_find_no_match() -> None:
    """No match at all returns None."""
    tracklist = [{"id": 1, "title": "Alpha"}]
    assert _find_in_tracklist("Zeta", tracklist) is None
