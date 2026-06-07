"""Tests for resolver.search_deezer_free — free-text Deezer search."""

from unittest.mock import patch

from music_manager.services.resolver import search_deezer_free


def test_search_free_returns_data_array() -> None:
    """Successful Deezer response → the inner data array is returned."""
    payload = {"data": [{"id": 1, "title": "T"}]}
    with patch(
        "music_manager.services.resolver.deezer_get", return_value=payload
    ):
        result = search_deezer_free("billie eilish")
    assert result == [{"id": 1, "title": "T"}]


def test_search_free_empty_query_short_circuits() -> None:
    """Empty/whitespace queries skip the HTTP call entirely."""
    with patch("music_manager.services.resolver.deezer_get") as mock_get:
        assert search_deezer_free("") == []
        assert search_deezer_free("   ") == []
    mock_get.assert_not_called()


def test_search_free_url_encodes_query() -> None:
    """The query is URL-encoded before being concatenated."""
    with patch(
        "music_manager.services.resolver.deezer_get", return_value={"data": []}
    ) as mock_get:
        search_deezer_free("billie eilish & friends")
    url = mock_get.call_args[0][0]
    assert "billie%20eilish" in url or "billie+eilish" in url
    assert "%26" in url  # & encoded


def test_search_free_caps_limit() -> None:
    """A limit greater than the API ceiling is clamped to 50."""
    with patch(
        "music_manager.services.resolver.deezer_get", return_value={"data": []}
    ) as mock_get:
        search_deezer_free("q", limit=500)
    assert "limit=50" in mock_get.call_args[0][0]


def test_search_free_clamps_low_limit() -> None:
    """A non-positive limit becomes 1 — never zero (would always return empty)."""
    with patch(
        "music_manager.services.resolver.deezer_get", return_value={"data": []}
    ) as mock_get:
        search_deezer_free("q", limit=0)
    assert "limit=1" in mock_get.call_args[0][0]


def test_search_free_returns_empty_on_deezer_failure() -> None:
    """deezer_get returns None on circuit-breaker / failure → CLI gets []."""
    with patch("music_manager.services.resolver.deezer_get", return_value=None):
        assert search_deezer_free("q") == []


def test_search_free_handles_unexpected_payload_shape() -> None:
    """If Deezer returns a non-list data field, we still return a list."""
    with patch(
        "music_manager.services.resolver.deezer_get",
        return_value={"data": "broken"},
    ):
        assert search_deezer_free("q") == []
