"""Tests for fetch_spotify_playlist_preview."""

import pytest

from music_manager.services import spotify


def _track(
    name: str = "Bad Guy",
    isrc: str = "USX12345",
    artists: list | None = None,
    cover: str = "https://c/x.jpg",
    preview: str = "https://p/x.mp3",
    is_local: bool = False,
) -> dict:
    return {
        "name": name,
        "is_local": is_local,
        "external_ids": {"isrc": isrc},
        "preview_url": preview,
        "artists": artists or [{"name": "Billie Eilish"}],
        "album": {"images": [{"url": cover}]} if cover else {"images": []},
    }


def _meta(
    name: str = "MyPlaylist",
    owner: str = "thomas",
    total: int = 10,
    image_url: str = "https://c/m.jpg",
) -> dict:
    return {
        "name": name,
        "owner": {"display_name": owner},
        "tracks": {"total": total},
        "images": [{"url": image_url}] if image_url else [],
    }


def _track_page(items: list, has_next: bool = False) -> dict:
    return {"items": items, "next": "https://x" if has_next else None}


def test_returns_empty_shape_on_missing_id() -> None:
    out = spotify.fetch_spotify_playlist_preview("")
    assert out == spotify._empty_preview()


def test_returns_empty_shape_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify, "spotify_get", lambda endpoint: None)
    out = spotify.fetch_spotify_playlist_preview("ABC123")
    assert out["tracks"] == []
    assert out["nb_tracks"] == 0


def test_extracts_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_get(endpoint: str) -> dict:
        if "/tracks" not in endpoint:
            return _meta(name="Chill", owner="bob", total=2, image_url="https://c.jpg")
        return _track_page([{"track": _track()}, {"track": _track(isrc="USX99999")}])

    monkeypatch.setattr(spotify, "spotify_get", mock_get)
    out = spotify.fetch_spotify_playlist_preview("ABC")
    assert out["name"] == "Chill"
    assert out["creator"] == "bob"
    assert out["nb_tracks"] == 2
    assert out["cover_url"] == "https://c.jpg"


def test_isrc_is_uppercased_and_deduped(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_get(endpoint: str) -> dict:
        if "/tracks" not in endpoint:
            return _meta()
        return _track_page(
            [
                {"track": _track(isrc="usx111")},
                {"track": _track(isrc="USX111")},
                {"track": _track(isrc="USX222")},
            ]
        )

    monkeypatch.setattr(spotify, "spotify_get", mock_get)
    out = spotify.fetch_spotify_playlist_preview("ABC")
    isrcs = [t["isrc"] for t in out["tracks"]]
    assert isrcs == ["USX111", "USX222"]


def test_skips_null_tracks(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_get(endpoint: str) -> dict:
        if "/tracks" not in endpoint:
            return _meta()
        return _track_page([{"track": None}, {"track": _track()}])

    monkeypatch.setattr(spotify, "spotify_get", mock_get)
    out = spotify.fetch_spotify_playlist_preview("ABC")
    assert len(out["tracks"]) == 1
    assert out["skipped_no_isrc"] == 1


def test_skips_local_tracks(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_get(endpoint: str) -> dict:
        if "/tracks" not in endpoint:
            return _meta()
        return _track_page(
            [{"track": _track(is_local=True)}, {"track": _track(isrc="USX1")}]
        )

    monkeypatch.setattr(spotify, "spotify_get", mock_get)
    out = spotify.fetch_spotify_playlist_preview("ABC")
    assert len(out["tracks"]) == 1
    assert out["skipped_no_isrc"] == 1


def test_skips_tracks_without_isrc(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_get(endpoint: str) -> dict:
        if "/tracks" not in endpoint:
            return _meta()
        return _track_page(
            [{"track": _track(isrc="")}, {"track": _track(isrc="USX1")}]
        )

    monkeypatch.setattr(spotify, "spotify_get", mock_get)
    out = spotify.fetch_spotify_playlist_preview("ABC")
    assert len(out["tracks"]) == 1
    assert out["skipped_no_isrc"] == 1


def test_paginates_via_next(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    pages = [
        _track_page(
            [{"track": _track(isrc=f"USX{i:04d}")} for i in range(100)], has_next=True
        ),
        _track_page([{"track": _track(isrc="USXLAST")}]),
    ]

    def mock_get(endpoint: str) -> dict | None:
        calls.append(endpoint)
        if "/tracks" not in endpoint:
            return _meta()
        return pages.pop(0) if pages else None

    monkeypatch.setattr(spotify, "spotify_get", mock_get)
    out = spotify.fetch_spotify_playlist_preview("ABC")
    assert len(out["tracks"]) == 101
    assert any("offset=0" in c for c in calls)
    assert any("offset=100" in c for c in calls)


def test_max_tracks_caps_results(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_get(endpoint: str) -> dict:
        if "/tracks" not in endpoint:
            return _meta()
        return _track_page(
            [{"track": _track(isrc=f"USX{i:04d}")} for i in range(100)], has_next=True
        )

    monkeypatch.setattr(spotify, "spotify_get", mock_get)
    out = spotify.fetch_spotify_playlist_preview("ABC", max_tracks=5)
    assert len(out["tracks"]) == 5


def test_track_fields_extracted(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_get(endpoint: str) -> dict:
        if "/tracks" not in endpoint:
            return _meta()
        return _track_page(
            [
                {
                    "track": {
                        "name": "Bad Guy",
                        "is_local": False,
                        "external_ids": {"isrc": "usxyz12345"},
                        "preview_url": "https://p/g.mp3",
                        "artists": [{"name": "Billie"}, {"name": "Ignored"}],
                        "album": {
                            "images": [
                                {"url": "https://big.jpg"},
                                {"url": "https://small.jpg"},
                            ]
                        },
                    }
                }
            ]
        )

    monkeypatch.setattr(spotify, "spotify_get", mock_get)
    out = spotify.fetch_spotify_playlist_preview("ABC")
    track = out["tracks"][0]
    assert track["title"] == "Bad Guy"
    assert track["artist"] == "Billie"
    assert track["isrc"] == "USXYZ12345"
    assert track["cover_url"] == "https://big.jpg"
    assert track["preview_url"] == "https://p/g.mp3"


def test_handles_special_chars_in_playlist_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Playlist ids are alphanum but the URL must still be encoded safely."""
    captured: list[str] = []

    def mock_get(endpoint: str) -> dict:
        captured.append(endpoint)
        if "/tracks" not in endpoint:
            return _meta()
        return _track_page([])

    monkeypatch.setattr(spotify, "spotify_get", mock_get)
    spotify.fetch_spotify_playlist_preview("ABC/EFG")
    assert any("ABC%2FEFG" in c for c in captured)
