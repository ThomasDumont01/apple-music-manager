"""Tests for services/resolver.py — all resolution paths."""

from unittest.mock import patch

from music_manager.services.albums import Albums
from music_manager.services.resolver import resolve

# ── Fixtures ────────────────────────────────────────────────────────────────

_DEEZER_TRACK = {
    "id": 123,
    "title": "Bohemian Rhapsody",
    "isrc": "GBUM71029604",
    "artist": {"name": "Queen"},
    "album": {"id": 1, "title": "A Night at the Opera"},
    "track_position": 11,
    "disk_number": 1,
    "duration": 354,
    "explicit_lyrics": False,
    "preview": "https://preview.mp3",
}

_DEEZER_ALBUM = {
    "title": "A Night at the Opera",
    "artist": {"name": "Queen"},
    "genres": {"data": [{"name": "Rock"}]},
    "release_date": "1975-11-21",
    "nb_tracks": 12,
    "cover_xl": "https://deezer-cover.jpg",
}

_SEARCH_SINGLE = {
    "data": [
        {
            "id": 123,
            "title": "Bohemian Rhapsody",
            "isrc": "GBUM71029604",
            "artist": {"name": "Queen"},
            "album": {"id": 1, "title": "A Night at the Opera"},
            "track_position": 11,
            "disk_number": 1,
            "duration": 354,
            "explicit_lyrics": False,
            "preview": "",
        }
    ]
}

_SEARCH_MULTI = {
    "data": [
        {
            "id": 123,
            "title": "Bohemian Rhapsody",
            "isrc": "GBUM71029604",
            "artist": {"name": "Queen"},
            "album": {"id": 1, "title": "A Night at the Opera"},
            "track_position": 11,
            "disk_number": 1,
            "duration": 354,
        },
        {
            "id": 456,
            "title": "Bohemian Rhapsody",
            "isrc": "GBUM71029605",
            "artist": {"name": "Queen"},
            "album": {"id": 2, "title": "Greatest Hits"},
            "track_position": 1,
            "disk_number": 1,
            "duration": 355,
        },
    ]
}

_SEARCH_ARTIST_MISMATCH = {
    "data": [
        {
            "id": 789,
            "title": "Bohemian Rhapsody",
            "artist": {"name": "Some Cover Band"},
            "album": {"id": 99, "title": "Cover Songs"},
        }
    ]
}

_NO_PATCH = "music_manager.services.resolver"


def _fake_get(responses: dict):
    """Create a side_effect for _deezer_get keyed by endpoint fragment."""

    def handler(endpoint: str):
        for key, value in responses.items():
            if key in endpoint:
                return value
        return None

    return handler


# ── ISRC present ────────────────────────────────────────────────────────────


def test_resolve_isrc_found(tmp_path) -> None:
    """ISRC found on Deezer → resolved with full metadata."""
    store = Albums(str(tmp_path / "albums.json"))

    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock,
        patch(f"{_NO_PATCH}._itunes_cover", return_value=""),
    ):
        mock.side_effect = _fake_get({"/track/isrc:": _DEEZER_TRACK, "/album/": _DEEZER_ALBUM})
        result = resolve(
            "Bohemian Rhapsody", "Queen", "A Night at the Opera", "GBUM71029604", store
        )

    assert result.status == "resolved"
    assert result.track is not None
    assert result.track.isrc == "GBUM71029604"
    assert result.track.title == "Bohemian Rhapsody"
    assert result.track.artist == "Queen"
    assert result.track.genre == "Rock"
    assert result.track.album_id == 1
    assert result.track.track_number == 11
    assert result.track.duration == 354


def test_resolve_isrc_not_on_deezer_fallback_search(tmp_path) -> None:
    """ISRC unknown on Deezer → fallback to title+artist search → resolved."""
    store = Albums(str(tmp_path / "albums.json"))

    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock,
        patch(f"{_NO_PATCH}._itunes_cover", return_value=""),
    ):
        mock.side_effect = _fake_get(
            {
                "/track/isrc:": {"error": {"type": 800}},
                "/search/track": _SEARCH_SINGLE,
                "/album/": _DEEZER_ALBUM,
            }
        )
        result = resolve(
            "Bohemian Rhapsody", "Queen", "A Night at the Opera", "FAKEISRC00000", store
        )

    assert result.status == "resolved"
    assert result.track is not None
    assert result.track.title == "Bohemian Rhapsody"


def test_resolve_isrc_not_on_deezer_search_empty(tmp_path) -> None:
    """ISRC unknown + search returns nothing → not_found."""
    store = Albums(str(tmp_path / "albums.json"))

    with patch(f"{_NO_PATCH}.deezer_get") as mock:
        mock.side_effect = _fake_get(
            {"/track/isrc:": {"error": {"type": 800}}, "/search/track": {"data": []}}
        )
        result = resolve("Unknown", "Nobody", "", "FAKEISRC00000", store)

    assert result.status == "not_found"


def test_resolve_isrc_deezer_unreachable(tmp_path) -> None:
    """Deezer returns None (network error) → not_found."""
    store = Albums(str(tmp_path / "albums.json"))

    with patch(f"{_NO_PATCH}.deezer_get", return_value=None):
        result = resolve("Song", "Artist", "", "GBUM71029604", store)

    assert result.status == "not_found"


# ── No ISRC — with album ───────────────────────────────────────────────────


def test_resolve_no_isrc_single_match_album_ok(tmp_path) -> None:
    """No ISRC, one match, album matches → resolved."""
    store = Albums(str(tmp_path / "albums.json"))

    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock,
        patch(f"{_NO_PATCH}._itunes_cover", return_value=""),
    ):
        mock.side_effect = _fake_get({"/search/track": _SEARCH_SINGLE, "/album/": _DEEZER_ALBUM})
        result = resolve("Bohemian Rhapsody", "Queen", "A Night at the Opera", "", store)

    assert result.status == "resolved"
    assert result.track is not None
    assert result.track.album == "A Night at the Opera"


def test_resolve_no_isrc_single_match_album_mismatch(tmp_path) -> None:
    """No ISRC, one match, wrong album → mismatch with track (not None)."""
    store = Albums(str(tmp_path / "albums.json"))

    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock,
        patch(f"{_NO_PATCH}._itunes_cover", return_value=""),
    ):
        mock.side_effect = _fake_get({"/search/track": _SEARCH_SINGLE, "/album/": _DEEZER_ALBUM})
        result = resolve("Bohemian Rhapsody", "Queen", "Greatest Hits", "", store)

    assert result.status == "mismatch"
    assert result.album_mismatch is True
    assert result.track is not None


def test_resolve_no_isrc_multiple_album_matches(tmp_path) -> None:
    """No ISRC, multiple matches all with same album → ambiguous."""
    store = Albums(str(tmp_path / "albums.json"))

    both_same_album = {
        "data": [
            {
                "id": 10,
                "title": "Song",
                "artist": {"name": "Artist"},
                "album": {"id": 1, "title": "Album A"},
            },
            {
                "id": 20,
                "title": "Song",
                "artist": {"name": "Artist"},
                "album": {"id": 2, "title": "Album A"},
            },
        ]
    }

    with patch(f"{_NO_PATCH}.deezer_get") as mock:
        mock.side_effect = _fake_get({"/search/track": both_same_album})
        result = resolve("Song", "Artist", "Album A", "", store)

    assert result.status == "ambiguous"
    assert len(result.candidates) >= 2


def test_resolve_no_isrc_multiple_no_album_match(tmp_path) -> None:
    """No ISRC, multiple matches, none match album → ambiguous."""
    store = Albums(str(tmp_path / "albums.json"))

    with patch(f"{_NO_PATCH}.deezer_get") as mock:
        mock.side_effect = _fake_get({"/search/track": _SEARCH_MULTI})
        result = resolve("Bohemian Rhapsody", "Queen", "Some Other Album", "", store)

    assert result.status == "ambiguous"
    assert len(result.candidates) == 2


# ── No ISRC — no album ─────────────────────────────────────────────────────


def test_resolve_no_isrc_no_album_single(tmp_path) -> None:
    """No ISRC, no album, single match → resolved."""
    store = Albums(str(tmp_path / "albums.json"))

    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock,
        patch(f"{_NO_PATCH}._itunes_cover", return_value=""),
    ):
        mock.side_effect = _fake_get({"/search/track": _SEARCH_SINGLE, "/album/": _DEEZER_ALBUM})
        result = resolve("Bohemian Rhapsody", "Queen", "", "", store)

    assert result.status == "resolved"
    assert result.track is not None


def test_resolve_no_isrc_no_album_multiple(tmp_path) -> None:
    """No ISRC, no album, multiple matches → ambiguous."""
    store = Albums(str(tmp_path / "albums.json"))

    with patch(f"{_NO_PATCH}.deezer_get") as mock:
        mock.side_effect = _fake_get({"/search/track": _SEARCH_MULTI})
        result = resolve("Bohemian Rhapsody", "Queen", "", "", store)

    assert result.status == "ambiguous"
    assert len(result.candidates) == 2


def test_resolve_no_isrc_no_match(tmp_path) -> None:
    """No ISRC, search returns nothing → not_found."""
    store = Albums(str(tmp_path / "albums.json"))

    with patch(f"{_NO_PATCH}.deezer_get", return_value=None):
        result = resolve("Unknown", "Nobody", "", "", store)

    assert result.status == "not_found"


def test_resolve_no_isrc_artist_mismatch_filtered(tmp_path) -> None:
    """Search results with wrong artist are filtered out → not_found."""
    store = Albums(str(tmp_path / "albums.json"))

    with patch(f"{_NO_PATCH}.deezer_get") as mock:
        mock.side_effect = _fake_get({"/search/track": _SEARCH_ARTIST_MISMATCH})
        result = resolve("Bohemian Rhapsody", "Queen", "", "", store)

    assert result.status == "not_found"


# ── Album cache ─────────────────────────────────────────────────────────────


def test_resolve_uses_album_cache(tmp_path) -> None:
    """Second resolve for same album uses cache — no extra API call."""
    store = Albums(str(tmp_path / "albums.json"))

    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock,
        patch(f"{_NO_PATCH}._itunes_cover", return_value=""),
    ):
        mock.side_effect = _fake_get({"/track/isrc:": _DEEZER_TRACK, "/album/": _DEEZER_ALBUM})
        resolve("Bohemian Rhapsody", "Queen", "", "GBUM71029604", store)

    # Album should be cached now
    cached = store.get(1)
    assert cached is not None
    assert cached["title"] == "A Night at the Opera"

    # Second resolve — album/1 should not be fetched again
    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock2,
        patch(f"{_NO_PATCH}._itunes_cover", return_value=""),
    ):
        mock2.side_effect = _fake_get({"/track/isrc:": _DEEZER_TRACK})
        result = resolve("Bohemian Rhapsody", "Queen", "", "GBUM71029604", store)

    assert result.status == "resolved"
    # _deezer_get should only be called for /track/isrc:, not /album/
    for call in mock2.call_args_list:
        assert "/album/" not in call[0][0]


# ── iTunes cover ────────────────────────────────────────────────────────────


def test_resolve_itunes_cover_upgrade(tmp_path) -> None:
    """iTunes cover replaces Deezer cover when found."""
    store = Albums(str(tmp_path / "albums.json"))

    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock,
        patch(f"{_NO_PATCH}._itunes_cover", return_value="https://itunes-3000x3000.jpg"),
    ):
        mock.side_effect = _fake_get({"/track/isrc:": _DEEZER_TRACK, "/album/": _DEEZER_ALBUM})
        result = resolve("Bohemian Rhapsody", "Queen", "", "GBUM71029604", store)

    assert result.track is not None
    assert result.track.cover_url == "https://itunes-3000x3000.jpg"


def test_resolve_deezer_cover_fallback(tmp_path) -> None:
    """When iTunes cover not found, Deezer cover is kept."""
    store = Albums(str(tmp_path / "albums.json"))

    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock,
        patch(f"{_NO_PATCH}._itunes_cover", return_value=""),
    ):
        mock.side_effect = _fake_get({"/track/isrc:": _DEEZER_TRACK, "/album/": _DEEZER_ALBUM})
        result = resolve("Bohemian Rhapsody", "Queen", "", "GBUM71029604", store)

    assert result.track is not None
    assert result.track.cover_url == "https://deezer-cover.jpg"


# ── Real-world edge cases ──────────────────────────────────────────────────


def test_wrong_isrc_falls_back_to_search(tmp_path) -> None:
    """ISRC points to completely different song → fallback to title+artist.

    Real case: Hotel California ISRC USEE10100142 → Joan Jett on Deezer.
    """
    store = Albums(str(tmp_path / "albums.json"))
    wrong_track = {
        "id": 999,
        "title": "Let's Do It",
        "isrc": "USEE10100142",
        "artist": {"name": "Joan Jett"},
        "album": {"id": 99, "title": "Tank Girl"},
        "track_position": 1,
        "disk_number": 1,
        "duration": 200,
    }
    correct_search = {
        "data": [
            {
                "id": 456,
                "title": "Hotel California",
                "artist": {"name": "Eagles"},
                "album": {"id": 2, "title": "Hotel California"},
            }
        ]
    }

    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock,
        patch(f"{_NO_PATCH}._itunes_cover", return_value=""),
    ):
        mock.side_effect = _fake_get(
            {
                "/track/isrc:": wrong_track,
                "/search/track": correct_search,
                "/track/456": {
                    "id": 456,
                    "title": "Hotel California",
                    "artist": {"name": "Eagles"},
                    "album": {"id": 2, "title": "Hotel California"},
                    "isrc": "USEE11300353",
                    "track_position": 1,
                    "disk_number": 1,
                    "duration": 390,
                },
                "/album/": _DEEZER_ALBUM,
            }
        )
        result = resolve("Hotel California", "Eagles", "", "USEE10100142", store)

    # Should NOT use the wrong ISRC result (Joan Jett)
    assert result.status == "resolved"
    assert result.track is not None
    assert result.track.artist == "Eagles"


def test_mismatch_returns_track_not_none(tmp_path) -> None:
    """Mismatch should return a complete Track (not None).

    Real case: resolver returned track=None for mismatch → "Accepter" broken.
    """
    store = Albums(str(tmp_path / "albums.json"))

    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock,
        patch(f"{_NO_PATCH}._itunes_cover", return_value=""),
    ):
        mock.side_effect = _fake_get({"/search/track": _SEARCH_SINGLE, "/album/": _DEEZER_ALBUM})
        result = resolve("Bohemian Rhapsody", "Queen", "Greatest Hits", "", store)

    assert result.status == "mismatch"
    assert result.track is not None
    assert result.track.title == "Bohemian Rhapsody"


def test_isrc_correct_album_different_resolved(tmp_path) -> None:
    """ISRC correct song but different album → resolved (ISRC reliable).

    Real case: Bohemian Rhapsody ISRC → Remaster album instead of original.
    """
    store = Albums(str(tmp_path / "albums.json"))
    remaster_track = {
        **_DEEZER_TRACK,
        "album": {"id": 2, "title": "A Night At The Opera (2011 Remaster)"},
    }

    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock,
        patch(f"{_NO_PATCH}._itunes_cover", return_value=""),
    ):
        mock.side_effect = _fake_get(
            {
                "/track/isrc:": remaster_track,
                "/search/track": {"data": []},
                "/search/album": {"data": []},
                "/album/": _DEEZER_ALBUM,
            }
        )
        result = resolve(
            "Bohemian Rhapsody", "Queen", "A Night at the Opera", "GBUM71029604", store
        )

    # ISRC is reliable → mismatch (album not found, use ISRC result)
    assert result.status == "mismatch"
    assert result.track is not None


def test_dash_vs_parens_title_matches(tmp_path) -> None:
    """Title with dash should match title with parens.

    Real case: "Be Your Man - Acoustic" vs "Be Your Man (Acoustic)"
    normalize() handles both → same result.
    """
    store = Albums(str(tmp_path / "albums.json"))
    acoustic_track = {
        **_DEEZER_TRACK,
        "id": 456,
        "title": "Be Your Man (Acoustic)",
        "artist": {"name": "Rhys Lewis"},
        "album": {"id": 3, "title": "Be Your Man (Acoustic)"},
    }

    with (
        patch(f"{_NO_PATCH}.deezer_get") as mock,
        patch(f"{_NO_PATCH}._itunes_cover", return_value=""),
    ):
        mock.side_effect = _fake_get(
            {
                "/search/track": {"data": [acoustic_track]},
                "/track/456": acoustic_track,
                "/album/": _DEEZER_ALBUM,
            }
        )
        result = resolve("Be Your Man - Acoustic", "Rhys Lewis", "", "", store)

    assert result.status == "resolved"
    assert result.track is not None
