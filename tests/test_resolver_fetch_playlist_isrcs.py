"""Tests for resolver.fetch_playlist_preview — full Deezer playlist → preview.

Pagination is mocked because Deezer's /playlist/{id}/tracks returns lite track
data; some items may lack `isrc`, in which case a /track/{id} hop is needed.
"""

from unittest.mock import patch

from music_manager.services.resolver import fetch_playlist_preview


def _meta(name: str = "My Playlist", nb_tracks: int = 0) -> dict:
    return {
        "id": 1,
        "title": name,
        "nb_tracks": nb_tracks,
        "creator": {"name": "thomas"},
    }


def _track(
    track_id: int,
    *,
    isrc: str | None = None,
    title: str = "T",
    artist: str = "A",
    cover: str = "https://e/x.jpg",
    preview: str = "https://e/p.mp3",
) -> dict:
    item: dict = {
        "id": track_id,
        "title": title,
        "artist": {"name": artist},
        "album": {"cover_medium": cover},
        "preview": preview,
    }
    if isrc is not None:
        item["isrc"] = isrc
    return item


def test_returns_empty_shape_on_unknown_playlist() -> None:
    """deezer_get returns None → shape is preserved but empty."""
    with patch("music_manager.services.resolver.deezer_get", return_value=None):
        result = fetch_playlist_preview(999)
    assert result == {
        "name": "",
        "creator": "",
        "nb_tracks": 0,
        "cover_url": "",
        "tracks": [],
        "skipped_no_isrc": 0,
    }


def test_cover_url_falls_back_through_resolutions() -> None:
    """picture_xl is preferred; we fall back if only picture_big / medium are set."""
    meta = {
        "id": 1,
        "title": "X",
        "nb_tracks": 0,
        "creator": {"name": "u"},
        "picture_xl": "https://e/xl.jpg",
        "picture_big": "https://e/big.jpg",
    }
    with patch(
        "music_manager.services.resolver.deezer_get",
        side_effect=lambda ep: meta if ep == "/playlist/1" else {"data": [], "next": None},
    ):
        result = fetch_playlist_preview(1)
    assert result["cover_url"] == "https://e/xl.jpg"

    meta_no_xl = {
        "id": 2,
        "title": "Y",
        "nb_tracks": 0,
        "creator": {"name": "u"},
        "picture_big": "https://e/big.jpg",
    }
    with patch(
        "music_manager.services.resolver.deezer_get",
        side_effect=lambda ep: meta_no_xl if ep == "/playlist/2" else {"data": [], "next": None},
    ):
        result = fetch_playlist_preview(2)
    assert result["cover_url"] == "https://e/big.jpg"


def test_collects_tracks_when_isrc_present_inline() -> None:
    """When tracks already carry `isrc`, no /track/{id} hop is needed."""
    meta = _meta("Chill", nb_tracks=2)
    page = {
        "data": [
            _track(1, isrc="frabc1234567", title="A", artist="X", cover="https://e/a.jpg"),
            _track(2, isrc="USXYZ7654321", title="B", artist="Y", cover="https://e/b.jpg"),
        ],
        "next": None,
    }

    def fake_get(endpoint: str) -> dict | None:
        if endpoint == "/playlist/42":
            return meta
        if endpoint.startswith("/playlist/42/tracks"):
            return page
        return None

    with patch("music_manager.services.resolver.deezer_get", side_effect=fake_get):
        result = fetch_playlist_preview(42)
    assert result["name"] == "Chill"
    assert result["creator"] == "thomas"
    assert result["nb_tracks"] == 2
    assert result["tracks"] == [
        {
            "isrc": "FRABC1234567",
            "title": "A",
            "artist": "X",
            "cover_url": "https://e/a.jpg",
            "preview_url": "https://e/p.mp3",
        },
        {
            "isrc": "USXYZ7654321",
            "title": "B",
            "artist": "Y",
            "cover_url": "https://e/b.jpg",
            "preview_url": "https://e/p.mp3",
        },
    ]
    assert result["skipped_no_isrc"] == 0


def test_falls_back_to_track_endpoint_for_missing_isrc() -> None:
    """A track without inline isrc → /track/{id} is consulted."""
    meta = _meta(nb_tracks=1)
    page = {"data": [_track(7, title="Solo", artist="Z")], "next": None}

    calls: list[str] = []

    def fake_get(endpoint: str) -> dict | None:
        calls.append(endpoint)
        if endpoint == "/playlist/1":
            return meta
        if endpoint.startswith("/playlist/1/tracks"):
            return page
        if endpoint == "/track/7":
            return {"id": 7, "isrc": "GBUM71234567"}
        return None

    with patch("music_manager.services.resolver.deezer_get", side_effect=fake_get):
        result = fetch_playlist_preview(1)
    assert len(result["tracks"]) == 1
    assert result["tracks"][0]["isrc"] == "GBUM71234567"
    assert result["tracks"][0]["title"] == "Solo"
    assert result["tracks"][0]["artist"] == "Z"
    assert result["skipped_no_isrc"] == 0
    assert "/track/7" in calls


def test_track_without_isrc_anywhere_is_counted_as_skipped() -> None:
    """If neither the playlist track nor /track/{id} yields an ISRC → skipped."""
    meta = _meta(nb_tracks=2)
    page = {
        "data": [
            _track(10, isrc="frabc1111111", title="Has it"),
            _track(11, title="No ISRC"),
        ],
        "next": None,
    }

    def fake_get(endpoint: str) -> dict | None:
        if endpoint == "/playlist/9":
            return meta
        if endpoint.startswith("/playlist/9/tracks"):
            return page
        if endpoint == "/track/11":
            return {"id": 11, "isrc": ""}
        return None

    with patch("music_manager.services.resolver.deezer_get", side_effect=fake_get):
        result = fetch_playlist_preview(9)
    assert len(result["tracks"]) == 1
    assert result["tracks"][0]["isrc"] == "FRABC1111111"
    assert result["skipped_no_isrc"] == 1


def test_dedupes_isrcs_preserving_order() -> None:
    """Same ISRC twice (e.g. live + studio re-release) → kept once, first wins."""
    meta = _meta(nb_tracks=3)
    page = {
        "data": [
            _track(1, isrc="USABC0000001", title="A"),
            _track(2, isrc="USABC0000002", title="B"),
            _track(3, isrc="usabc0000001", title="A bis"),
        ],
        "next": None,
    }

    def fake_get(endpoint: str) -> dict | None:
        if endpoint == "/playlist/3":
            return meta
        if endpoint.startswith("/playlist/3/tracks"):
            return page
        return None

    with patch("music_manager.services.resolver.deezer_get", side_effect=fake_get):
        result = fetch_playlist_preview(3)
    isrcs = [t["isrc"] for t in result["tracks"]]
    assert isrcs == ["USABC0000001", "USABC0000002"]
    titles = [t["title"] for t in result["tracks"]]
    assert titles == ["A", "B"]  # first occurrence wins


def test_paginates_through_next_pages() -> None:
    """Deezer caps each call at 100 tracks → we follow ?index=100 until empty."""
    meta = _meta(nb_tracks=150)
    page1 = {
        "data": [_track(i, isrc=f"USAAA{i:07d}", title=f"T{i}") for i in range(100)],
        "next": "https://api.deezer.com/playlist/5/tracks?index=100&limit=100",
    }
    page2 = {
        "data": [_track(i, isrc=f"USAAA{i:07d}", title=f"T{i}") for i in range(100, 150)],
        "next": None,
    }

    calls: list[str] = []

    def fake_get(endpoint: str) -> dict | None:
        calls.append(endpoint)
        if endpoint == "/playlist/5":
            return meta
        if endpoint.startswith("/playlist/5/tracks") and "index=100" not in endpoint:
            return page1
        if endpoint.startswith("/playlist/5/tracks") and "index=100" in endpoint:
            return page2
        return None

    with patch("music_manager.services.resolver.deezer_get", side_effect=fake_get):
        result = fetch_playlist_preview(5)
    assert len(result["tracks"]) == 150
    assert result["tracks"][0]["isrc"] == "USAAA0000000"
    assert result["tracks"][-1]["isrc"] == "USAAA0000149"
    assert any("index=100" in c for c in calls)


def test_respects_max_tracks_cap() -> None:
    """max_tracks bounds how many tracks we fetch — extras are silently dropped."""
    meta = _meta(nb_tracks=200)
    page = {
        "data": [_track(i, isrc=f"USBBB{i:07d}", title=f"T{i}") for i in range(100)],
        "next": "https://api.deezer.com/playlist/6/tracks?index=100&limit=100",
    }

    def fake_get(endpoint: str) -> dict | None:
        if endpoint == "/playlist/6":
            return meta
        if endpoint.startswith("/playlist/6/tracks"):
            return page  # always return same page; we should stop early anyway
        return None

    with patch("music_manager.services.resolver.deezer_get", side_effect=fake_get):
        result = fetch_playlist_preview(6, max_tracks=50)
    assert len(result["tracks"]) == 50


def test_invalid_playlist_id_short_circuits() -> None:
    """Zero / negative IDs never hit the network."""
    with patch("music_manager.services.resolver.deezer_get") as mock_get:
        result = fetch_playlist_preview(0)
    mock_get.assert_not_called()
    assert result["tracks"] == []
    assert result["nb_tracks"] == 0


def test_cover_url_falls_back_to_cover_when_medium_absent() -> None:
    """If album has no cover_medium, fall back to cover (lower res)."""
    meta = _meta(nb_tracks=1)
    page = {
        "data": [
            {
                "id": 1,
                "title": "T",
                "isrc": "USCCC0000001",
                "artist": {"name": "A"},
                "album": {"cover": "https://e/low.jpg"},  # no cover_medium
            }
        ],
        "next": None,
    }

    def fake_get(endpoint: str) -> dict | None:
        if endpoint == "/playlist/7":
            return meta
        if endpoint.startswith("/playlist/7/tracks"):
            return page
        return None

    with patch("music_manager.services.resolver.deezer_get", side_effect=fake_get):
        result = fetch_playlist_preview(7)
    assert result["tracks"][0]["cover_url"] == "https://e/low.jpg"


def test_preview_url_propagated_from_deezer_item() -> None:
    """preview field is forwarded verbatim — empty string if missing."""
    meta = _meta(nb_tracks=2)
    page = {
        "data": [
            {
                "id": 1,
                "title": "With",
                "isrc": "USDDD0000001",
                "artist": {"name": "A"},
                "album": {"cover_medium": "https://e/c.jpg"},
                "preview": "https://e-cdns-preview.deezer.com/snippet.mp3",
            },
            {
                "id": 2,
                "title": "Without",
                "isrc": "USDDD0000002",
                "artist": {"name": "B"},
                "album": {"cover_medium": "https://e/d.jpg"},
                # no `preview` key
            },
        ],
        "next": None,
    }

    def fake_get(endpoint: str) -> dict | None:
        if endpoint == "/playlist/8":
            return meta
        if endpoint.startswith("/playlist/8/tracks"):
            return page
        return None

    with patch("music_manager.services.resolver.deezer_get", side_effect=fake_get):
        result = fetch_playlist_preview(8)
    assert result["tracks"][0]["preview_url"] == "https://e-cdns-preview.deezer.com/snippet.mp3"
    assert result["tracks"][1]["preview_url"] == ""
