"""Tests for resolver.search_deezer_playlists — free-text Deezer playlist search."""

from unittest.mock import patch

from music_manager.services.resolver import search_deezer_playlists


def test_search_playlists_returns_data_array() -> None:
    """Successful Deezer response → the inner data array is returned."""
    payload = {
        "data": [
            {
                "id": 908622995,
                "title": "Lofi Hip Hop",
                "nb_tracks": 42,
                "picture_medium": "https://e-cdns.example/lofi.jpg",
                "user": {"name": "deezer"},
            }
        ]
    }
    with patch(
        "music_manager.services.resolver.deezer_get", return_value=payload
    ) as mock_get:
        result = search_deezer_playlists("lofi", limit=5)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == 908622995
    # Endpoint targets /search/playlist (not /search/track)
    url = mock_get.call_args[0][0]
    assert url.startswith("/search/playlist?")


def test_search_playlists_empty_query_short_circuits() -> None:
    """Empty/whitespace queries skip the HTTP call entirely."""
    with patch("music_manager.services.resolver.deezer_get") as mock_get:
        assert search_deezer_playlists("") == []
        assert search_deezer_playlists("   ") == []
    mock_get.assert_not_called()


def test_search_playlists_url_encodes_query() -> None:
    """The query is URL-encoded (spaces + special chars)."""
    with patch(
        "music_manager.services.resolver.deezer_get", return_value={"data": []}
    ) as mock_get:
        search_deezer_playlists("chill & cosy")
    url = mock_get.call_args[0][0]
    assert "chill%20" in url or "chill+" in url
    assert "%26" in url  # & encoded


def test_search_playlists_caps_limit() -> None:
    """A limit greater than the API ceiling is clamped to 50."""
    with patch(
        "music_manager.services.resolver.deezer_get", return_value={"data": []}
    ) as mock_get:
        search_deezer_playlists("q", limit=500)
    assert "limit=50" in mock_get.call_args[0][0]


def test_search_playlists_clamps_low_limit() -> None:
    """A non-positive limit becomes 1 — never zero."""
    with patch(
        "music_manager.services.resolver.deezer_get", return_value={"data": []}
    ) as mock_get:
        search_deezer_playlists("q", limit=0)
    assert "limit=1" in mock_get.call_args[0][0]


def test_search_playlists_returns_empty_on_deezer_failure() -> None:
    """deezer_get returns None on circuit-breaker / failure → CLI gets []."""
    with patch("music_manager.services.resolver.deezer_get", return_value=None):
        assert search_deezer_playlists("q") == []


def test_search_playlists_handles_non_list_data() -> None:
    """If Deezer returns a non-list data field, we still return a list."""
    with patch(
        "music_manager.services.resolver.deezer_get",
        return_value={"data": "broken"},
    ):
        assert search_deezer_playlists("q") == []
