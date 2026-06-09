"""Tests for fetch_user_playlists + count_liked_tracks."""

import pytest

from music_manager.services import spotify


def _playlist(
    spotify_id: str = "p1",
    name: str = "Workout",
    total: int = 10,
    owner_name: str = "thomas",
    image_url: str = "https://i/a.jpg",
) -> dict:
    return {
        "id": spotify_id,
        "name": name,
        "owner": {"display_name": owner_name},
        "images": [{"url": image_url}] if image_url else [],
        "tracks": {"total": total},
    }


def _page(items: list, has_next: bool = False) -> dict:
    return {
        "items": items,
        "next": "https://api.spotify.com/v1/me/playlists?offset=50" if has_next else None,
    }


def test_fetch_playlists_single_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify, "spotify_get", lambda endpoint: _page([_playlist()])
    )
    out = spotify.fetch_user_playlists()
    assert out == [
        {
            "spotify_id": "p1",
            "title": "Workout",
            "nb_tracks": 10,
            "picture_url": "https://i/a.jpg",
            "creator": "thomas",
        }
    ]


def test_fetch_playlists_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        _page(
            [_playlist(spotify_id=f"p{i}", name=f"P{i}") for i in range(50)],
            has_next=True,
        ),
        _page([_playlist(spotify_id="p51", name="Last")]),
    ]
    calls: list[str] = []

    def mock_get(endpoint: str) -> dict | None:
        calls.append(endpoint)
        return pages.pop(0) if pages else None

    monkeypatch.setattr(spotify, "spotify_get", mock_get)
    out = spotify.fetch_user_playlists()
    assert len(out) == 51
    assert "offset=0" in calls[0]
    assert "offset=50" in calls[1]


def test_fetch_playlists_respects_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "spotify_get",
        lambda endpoint: _page(
            [_playlist(spotify_id=f"p{i}", name=f"P{i}") for i in range(50)],
            has_next=True,
        ),
    )
    out = spotify.fetch_user_playlists(max_playlists=3)
    assert len(out) == 3


def test_fetch_playlists_empty_when_api_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify, "spotify_get", lambda endpoint: None)
    assert spotify.fetch_user_playlists() == []


def test_fetch_playlists_skips_invalid_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "spotify_get",
        lambda endpoint: _page([_playlist(), None, "garbage"]),  # type: ignore[list-item]
    )
    out = spotify.fetch_user_playlists()
    assert len(out) == 1


def test_fetch_playlists_handles_missing_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "spotify_get",
        lambda endpoint: _page([_playlist(image_url="")]),
    )
    out = spotify.fetch_user_playlists()
    assert out[0]["picture_url"] == ""


def test_count_liked_tracks_reads_total(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify, "spotify_get", lambda endpoint: {"total": 1337, "items": []}
    )
    assert spotify.count_liked_tracks() == 1337


def test_count_liked_tracks_returns_zero_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(spotify, "spotify_get", lambda endpoint: None)
    assert spotify.count_liked_tracks() == 0
