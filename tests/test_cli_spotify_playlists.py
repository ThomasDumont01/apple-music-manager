"""Tests for music_manager.cli.spotify_playlists."""

import json

import pytest

from music_manager.cli import spotify_playlists


def test_returns_playlists_with_liked_first(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_playlists, "is_authenticated", lambda: True)
    monkeypatch.setattr(
        spotify_playlists,
        "fetch_user_playlists",
        lambda: [
            {
                "spotify_id": "p1",
                "title": "Workout",
                "nb_tracks": 10,
                "picture_url": "https://c.jpg",
                "creator": "thomas",
            }
        ],
    )
    monkeypatch.setattr(spotify_playlists, "count_liked_tracks", lambda: 1234)
    exit_code = spotify_playlists.main([])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["spotify_id"] == "liked"
    assert out[0]["title"] == "♥ Titres likés"
    assert out[0]["nb_tracks"] == 1234
    assert out[1]["spotify_id"] == "p1"
    assert out[1]["title"] == "Workout"


def test_returns_error_when_not_authenticated(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_playlists, "is_authenticated", lambda: False)
    exit_code = spotify_playlists.main([])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "not_authenticated"


def test_liked_entry_appears_even_with_zero_likes(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_playlists, "is_authenticated", lambda: True)
    monkeypatch.setattr(spotify_playlists, "fetch_user_playlists", lambda: [])
    monkeypatch.setattr(spotify_playlists, "count_liked_tracks", lambda: 0)
    spotify_playlists.main([])
    out = json.loads(capsys.readouterr().out)
    assert out[0]["spotify_id"] == "liked"
    assert out[0]["nb_tracks"] == 0


def test_resolver_exception_returns_error(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_playlists, "is_authenticated", lambda: True)

    def raise_err() -> int:
        raise RuntimeError("circuit open")

    monkeypatch.setattr(spotify_playlists, "count_liked_tracks", raise_err)
    exit_code = spotify_playlists.main([])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert "circuit open" in out["error"]
