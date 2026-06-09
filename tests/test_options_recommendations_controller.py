"""Tests for music_manager/options/recommendations.py — playlist seed helpers."""

from unittest.mock import MagicMock

import pytest

from music_manager.options.recommendations import (
    RecommendationModeConfig,
    extract_playlist_seeds,
    validate_playlist_exists,
)
from music_manager.services.tracks import Tracks


@pytest.fixture
def tracks(tmp_path) -> Tracks:
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "AP1",
        {"isrc": "ISRC1", "title": "Song A", "artist": "Artist A", "genre": "Rock"},
    )
    store.add(
        "AP2",
        {"isrc": "ISRC2", "title": "Song B", "artist": "Artist B", "genre": "Pop"},
    )
    store.add(
        "AP3",
        {"isrc": "", "title": "No ISRC", "artist": "Artist C"},  # missing ISRC
    )
    store.add(
        "AP4",
        {"isrc": "ISRC4", "title": "", "artist": "Artist D"},  # missing title
    )
    return store


@pytest.fixture
def apple_stub():
    return MagicMock()


# ── extract_playlist_seeds ──────────────────────────────────────────────────


def test_extract_playlist_seeds_returns_isrc_title_artist(tracks, apple_stub) -> None:
    apple_stub.get_playlist_tracks.return_value = ["AP1", "AP2"]
    seeds = extract_playlist_seeds("Workout", tracks, apple_service=apple_stub)
    assert seeds == [
        ("ISRC1", "Song A", "Artist A"),
        ("ISRC2", "Song B", "Artist B"),
    ]
    apple_stub.get_playlist_tracks.assert_called_once_with("Workout")


def test_extract_playlist_seeds_skips_tracks_without_isrc(tracks, apple_stub) -> None:
    apple_stub.get_playlist_tracks.return_value = ["AP1", "AP3"]
    seeds = extract_playlist_seeds("Workout", tracks, apple_service=apple_stub)
    assert [isrc for isrc, _, _ in seeds] == ["ISRC1"]


def test_extract_playlist_seeds_skips_unknown_apple_ids(tracks, apple_stub) -> None:
    apple_stub.get_playlist_tracks.return_value = ["AP_MISSING", "AP1"]
    seeds = extract_playlist_seeds("Workout", tracks, apple_service=apple_stub)
    assert [isrc for isrc, _, _ in seeds] == ["ISRC1"]


def test_extract_playlist_seeds_skips_missing_title_or_artist(tracks, apple_stub) -> None:
    apple_stub.get_playlist_tracks.return_value = ["AP4", "AP1"]
    seeds = extract_playlist_seeds("Workout", tracks, apple_service=apple_stub)
    assert [isrc for isrc, _, _ in seeds] == ["ISRC1"]


def test_extract_playlist_seeds_deduplicates_by_isrc(tracks, apple_stub) -> None:
    apple_stub.get_playlist_tracks.return_value = ["AP1", "AP1", "AP2"]
    seeds = extract_playlist_seeds("Workout", tracks, apple_service=apple_stub)
    assert [isrc for isrc, _, _ in seeds] == ["ISRC1", "ISRC2"]


def test_extract_playlist_seeds_respects_limit(tracks, apple_stub) -> None:
    apple_stub.get_playlist_tracks.return_value = ["AP1", "AP2"]
    seeds = extract_playlist_seeds(
        "Workout", tracks, apple_service=apple_stub, limit=1
    )
    assert len(seeds) == 1
    assert seeds[0][0] == "ISRC1"


def test_extract_playlist_seeds_empty_playlist_name(tracks, apple_stub) -> None:
    assert extract_playlist_seeds("", tracks, apple_service=apple_stub) == []
    apple_stub.get_playlist_tracks.assert_not_called()


def test_extract_playlist_seeds_applescript_failure_returns_empty(
    tracks, apple_stub
) -> None:
    apple_stub.get_playlist_tracks.side_effect = RuntimeError("boom")
    assert extract_playlist_seeds("Workout", tracks, apple_service=apple_stub) == []


def test_extract_playlist_seeds_uppercases_isrc(tmp_path, apple_stub) -> None:
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("AP1", {"isrc": "lower1", "title": "S", "artist": "A"})
    apple_stub.get_playlist_tracks.return_value = ["AP1"]
    seeds = extract_playlist_seeds("Test", tracks, apple_service=apple_stub)
    assert seeds[0][0] == "LOWER1"


# ── validate_playlist_exists ────────────────────────────────────────────────


def test_validate_playlist_exists_true(apple_stub) -> None:
    apple_stub.list_playlists.return_value = [("Workout", 12), ("Chill", 5)]
    assert validate_playlist_exists("Workout", apple_service=apple_stub) is True


def test_validate_playlist_exists_false(apple_stub) -> None:
    apple_stub.list_playlists.return_value = [("Other", 1)]
    assert validate_playlist_exists("Workout", apple_service=apple_stub) is False


def test_validate_playlist_exists_passes_exclude_folder(apple_stub) -> None:
    apple_stub.list_playlists.return_value = []
    validate_playlist_exists(
        "Workout", apple_service=apple_stub, folder_name="for me"
    )
    apple_stub.list_playlists.assert_called_once_with(exclude_folder="for me")


def test_validate_playlist_exists_empty_name(apple_stub) -> None:
    assert validate_playlist_exists("", apple_service=apple_stub) is False
    apple_stub.list_playlists.assert_not_called()


def test_validate_playlist_exists_apple_failure(apple_stub) -> None:
    apple_stub.list_playlists.side_effect = RuntimeError("boom")
    assert validate_playlist_exists("X", apple_service=apple_stub) is False


# ── RecommendationModeConfig ────────────────────────────────────────────────


def test_recommendation_mode_config_defaults() -> None:
    config = RecommendationModeConfig(mode="library")
    assert config.target_count == 20
    assert config.playlist_seed_name == ""
    assert config.extra == {}
