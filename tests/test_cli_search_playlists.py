"""Tests for music_manager/cli/search_playlists.py."""

import json
from unittest.mock import patch

import pytest

from music_manager.cli import search_playlists


def _dz_playlist(
    *,
    pid: int = 908622995,
    title: str = "Lofi Hip Hop",
    nb_tracks: int = 42,
    picture: str = "https://e-cdns.example/lofi.jpg",
    creator: str = "deezer",
) -> dict:
    return {
        "id": pid,
        "title": title,
        "nb_tracks": nb_tracks,
        "picture_medium": picture,
        "user": {"name": creator},
    }


def test_outputs_stable_json_schema(capsys: pytest.CaptureFixture) -> None:
    with patch(
        "music_manager.cli.search_playlists.search_deezer_playlists",
        return_value=[_dz_playlist()],
    ):
        exit_code = search_playlists.main(["lofi"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    item = payload[0]
    assert item["deezer_id"] == 908622995
    assert item["title"] == "Lofi Hip Hop"
    assert item["nb_tracks"] == 42
    assert item["picture_url"].startswith("https://")
    assert item["creator"] == "deezer"


def test_passes_limit_argument(capsys: pytest.CaptureFixture) -> None:
    with patch(
        "music_manager.cli.search_playlists.search_deezer_playlists",
        return_value=[],
    ) as mock_search:
        search_playlists.main(["query", "--limit", "5"])
    mock_search.assert_called_once_with("query", 5)


def test_default_limit_is_ten(capsys: pytest.CaptureFixture) -> None:
    with patch(
        "music_manager.cli.search_playlists.search_deezer_playlists",
        return_value=[],
    ) as mock_search:
        search_playlists.main(["query"])
    mock_search.assert_called_once_with("query", 10)


def test_returns_empty_array_for_empty_results(
    capsys: pytest.CaptureFixture,
) -> None:
    with patch(
        "music_manager.cli.search_playlists.search_deezer_playlists",
        return_value=[],
    ):
        exit_code = search_playlists.main(["nothing"])
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == []


def test_handles_resolver_exception(capsys: pytest.CaptureFixture) -> None:
    with patch(
        "music_manager.cli.search_playlists.search_deezer_playlists",
        side_effect=RuntimeError("Deezer offline"),
    ):
        exit_code = search_playlists.main(["query"])
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert "error" in payload
    assert "Deezer offline" in payload["error"]


def test_skips_non_dict_items(capsys: pytest.CaptureFixture) -> None:
    """Defensive: malformed Deezer payloads don't crash the CLI."""
    with patch(
        "music_manager.cli.search_playlists.search_deezer_playlists",
        return_value=[_dz_playlist(), "garbage", None, _dz_playlist(title="Other")],
    ):
        search_playlists.main(["q"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 2
    assert payload[1]["title"] == "Other"


def test_fallback_picture_field(capsys: pytest.CaptureFixture) -> None:
    """If picture_medium absent, fall back to picture (Deezer sometimes returns just `picture`)."""
    payload_in = [
        {
            "id": 1,
            "title": "T",
            "nb_tracks": 1,
            "picture": "https://e-cdns.example/p.jpg",
            "user": {"name": "x"},
        }
    ]
    with patch(
        "music_manager.cli.search_playlists.search_deezer_playlists",
        return_value=payload_in,
    ):
        search_playlists.main(["q"])
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["picture_url"] == "https://e-cdns.example/p.jpg"
