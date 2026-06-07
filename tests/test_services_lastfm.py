"""Tests for services/lastfm.py — wrapper API + circuit breaker + cache."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from music_manager.services import lastfm

FIXTURES = Path(__file__).parent / "data" / "lastfm"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _response(payload: dict | None, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else {}
    return resp


@pytest.fixture(autouse=True)
def _reset_lastfm_state():
    """Always start with a clean cache + open circuit between tests."""
    lastfm._reset_state_for_tests()
    yield
    lastfm._reset_state_for_tests()


@pytest.fixture
def with_api_key(monkeypatch: pytest.MonkeyPatch):
    """Provide a Last.fm API key for the duration of a test."""
    monkeypatch.setenv("LASTFM_API_KEY", "fake-key-for-tests")


# ── get_similar_tracks ──────────────────────────────────────────────────────


def test_get_similar_tracks_parses_response(with_api_key) -> None:
    """getsimilar fixture is decoded into name/artist/mbid/match dicts."""
    payload = _load("getSimilar_radiohead_creep.json")
    with patch("music_manager.services.lastfm.http_get", return_value=_response(payload)):
        results = lastfm.get_similar_tracks("Radiohead", "Creep")

    assert len(results) == 3
    assert results[0]["name"] == "Karma Police"
    assert results[0]["artist"] == "Radiohead"
    assert results[0]["mbid"] == "abcd-1234"
    assert results[0]["match"] == pytest.approx(0.95)
    assert results[2]["mbid"] == ""


def test_get_similar_tracks_empty_seed_returns_empty() -> None:
    """No artist or track → no API call, empty list."""
    with patch("music_manager.services.lastfm.http_get") as mock_http:
        assert lastfm.get_similar_tracks("", "Creep") == []
        assert lastfm.get_similar_tracks("Radiohead", "") == []
    mock_http.assert_not_called()


def test_get_similar_tracks_handles_empty_response(with_api_key) -> None:
    """Empty similartracks container → empty list, no crash."""
    payload = _load("getSimilar_empty.json")
    with patch("music_manager.services.lastfm.http_get", return_value=_response(payload)):
        assert lastfm.get_similar_tracks("Unknown", "Track") == []


def test_get_similar_tracks_single_item_dict(with_api_key) -> None:
    """Last.fm returns a dict instead of a list when only 1 item — handled."""
    payload = {
        "similartracks": {
            "track": {"name": "Solo", "artist": {"name": "Lone"}, "mbid": "x", "match": "0.5"}
        }
    }
    with patch("music_manager.services.lastfm.http_get", return_value=_response(payload)):
        results = lastfm.get_similar_tracks("Lone", "Solo")
    assert len(results) == 1
    assert results[0]["name"] == "Solo"


# ── get_top_tracks_by_tag ───────────────────────────────────────────────────


def test_get_top_tracks_by_tag_parses_response(with_api_key) -> None:
    """gettoptracks fixture is decoded correctly."""
    payload = _load("getTopTracks_chill.json")
    with patch("music_manager.services.lastfm.http_get", return_value=_response(payload)):
        results = lastfm.get_top_tracks_by_tag("chill")

    assert len(results) == 2
    assert results[0]["name"] == "Weightless"
    assert results[0]["artist"] == "Marconi Union"
    assert results[1]["artist"] == "Sleeping At Last"


def test_get_top_tracks_by_tag_empty_tag() -> None:
    """Empty tag → no API call, empty list."""
    with patch("music_manager.services.lastfm.http_get") as mock_http:
        assert lastfm.get_top_tracks_by_tag("") == []
    mock_http.assert_not_called()


# ── get_similar_artists ─────────────────────────────────────────────────────


def test_get_similar_artists_parses_response(with_api_key) -> None:
    """getsimilarartists fixture returns name/mbid/match."""
    payload = _load("getSimilarArtists_radiohead.json")
    with patch("music_manager.services.lastfm.http_get", return_value=_response(payload)):
        results = lastfm.get_similar_artists("Radiohead")
    assert results[0]["name"] == "Muse"
    assert results[0]["match"] == pytest.approx(0.72)


# ── Cache ───────────────────────────────────────────────────────────────────


def test_cache_hit_avoids_second_request(with_api_key) -> None:
    """Identical call → single HTTP request (cache hit on the 2nd)."""
    payload = _load("getSimilar_radiohead_creep.json")
    with patch(
        "music_manager.services.lastfm.http_get", return_value=_response(payload)
    ) as mock_http:
        lastfm.get_similar_tracks("Radiohead", "Creep")
        lastfm.get_similar_tracks("Radiohead", "Creep")
    assert mock_http.call_count == 1


def test_cache_miss_on_different_params(with_api_key) -> None:
    """Different artist/track → separate cache key → 2 HTTP requests."""
    payload = _load("getSimilar_radiohead_creep.json")
    with patch(
        "music_manager.services.lastfm.http_get", return_value=_response(payload)
    ) as mock_http:
        lastfm.get_similar_tracks("Radiohead", "Creep")
        lastfm.get_similar_tracks("Oasis", "Wonderwall")
    assert mock_http.call_count == 2


# ── Error handling ──────────────────────────────────────────────────────────


def test_no_api_key_returns_empty_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing key → returns [] without raising and without HTTP call."""
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)
    monkeypatch.setattr("music_manager.services.lastfm.load_config", lambda: {})
    with patch("music_manager.services.lastfm.http_get") as mock_http:
        assert lastfm.get_similar_tracks("Radiohead", "Creep") == []
    mock_http.assert_not_called()


def test_api_error_payload_returns_empty(with_api_key) -> None:
    """A payload with an 'error' field is treated as failure → []."""
    payload = _load("getSimilar_error_invalid_key.json")
    with patch("music_manager.services.lastfm.http_get", return_value=_response(payload)):
        assert lastfm.get_similar_tracks("Radiohead", "Creep") == []


def test_http_401_returns_empty(with_api_key) -> None:
    """HTTP 401 from Last.fm → empty list, no crash."""
    with patch(
        "music_manager.services.lastfm.http_get", return_value=_response({}, status=401)
    ):
        assert lastfm.get_similar_tracks("Radiohead", "Creep") == []


def test_http_exception_returns_empty(with_api_key) -> None:
    """Transport exception is swallowed."""
    with patch(
        "music_manager.services.lastfm.http_get", side_effect=ConnectionError("offline")
    ):
        assert lastfm.get_similar_tracks("Radiohead", "Creep") == []


def test_circuit_breaker_skips_after_threshold(with_api_key) -> None:
    """After CIRCUIT_THRESHOLD failures, subsequent calls bypass HTTP."""
    with patch(
        "music_manager.services.lastfm.http_get", return_value=_response({}, status=500)
    ) as mock_http:
        for _ in range(lastfm._CIRCUIT_THRESHOLD):
            lastfm.get_similar_tracks("A", f"T{_}")
        # Circuit is now open: the next call must NOT trigger http_get.
        before = mock_http.call_count
        lastfm.get_similar_tracks("X", "Y")
        assert mock_http.call_count == before
