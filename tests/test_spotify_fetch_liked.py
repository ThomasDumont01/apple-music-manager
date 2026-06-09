"""Tests for fetch_liked_tracks."""

import pytest

from music_manager.services import spotify


def _track(isrc: str = "USX1", title: str = "T", is_local: bool = False) -> dict:
    return {
        "name": title,
        "is_local": is_local,
        "external_ids": {"isrc": isrc},
        "preview_url": "",
        "artists": [{"name": "A"}],
        "album": {"images": [{"url": "https://c/x.jpg"}]},
    }


def _page(items: list, total: int = 10, has_next: bool = False) -> dict:
    return {
        "items": items,
        "total": total,
        "next": "https://x" if has_next else None,
    }


def test_returns_named_liked_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "spotify_get",
        lambda endpoint: _page([{"track": _track()}]),
    )
    out = spotify.fetch_liked_tracks()
    assert out["name"] == "♥ Titres likés"
    assert out["creator"] == ""
    assert out["nb_tracks"] == 10
    assert len(out["tracks"]) == 1


def test_paginates_via_next(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        _page(
            [{"track": _track(isrc=f"USX{i:04d}")} for i in range(50)],
            total=100,
            has_next=True,
        ),
        _page([{"track": _track(isrc="USXLAST")}], total=100),
    ]
    monkeypatch.setattr(
        spotify, "spotify_get", lambda endpoint: pages.pop(0) if pages else None
    )
    out = spotify.fetch_liked_tracks()
    assert len(out["tracks"]) == 51
    assert out["nb_tracks"] == 100


def test_caps_max_tracks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "spotify_get",
        lambda endpoint: _page(
            [{"track": _track(isrc=f"USX{i:04d}")} for i in range(50)],
            total=500,
            has_next=True,
        ),
    )
    out = spotify.fetch_liked_tracks(max_tracks=3)
    assert len(out["tracks"]) == 3


def test_skips_local_and_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "spotify_get",
        lambda endpoint: _page(
            [
                {"track": None},
                {"track": _track(is_local=True)},
                {"track": _track(isrc="USX1")},
            ],
            total=3,
        ),
    )
    out = spotify.fetch_liked_tracks()
    assert len(out["tracks"]) == 1
    assert out["skipped_no_isrc"] == 2


def test_empty_when_api_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify, "spotify_get", lambda endpoint: None)
    out = spotify.fetch_liked_tracks()
    assert out["tracks"] == []
    assert out["name"] == "♥ Titres likés"
