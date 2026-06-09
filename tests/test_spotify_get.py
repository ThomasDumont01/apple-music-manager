"""Tests for spotify_get — cache, retry, circuit breaker."""

from unittest.mock import MagicMock

import pytest

from music_manager.services import spotify


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with empty cache + clean breaker state."""
    spotify._SPOTIFY_CACHE.clear()
    monkeypatch.setattr(spotify, "_consecutive_failures_sp", 0)
    monkeypatch.setattr(spotify, "_circuit_open_until_sp", 0.0)


def _mock_response(status: int, body: dict | None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    if body is None:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = body
    return resp


def test_returns_data_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify, "_ensure_fresh_access_token", lambda **_k: "AT")
    monkeypatch.setattr(
        spotify, "http_get", lambda url, headers=None: _mock_response(200, {"id": "p1"})
    )
    assert spotify.spotify_get("/playlists/abc") == {"id": "p1"}


def test_returns_none_without_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify, "_ensure_fresh_access_token", lambda **_k: "")
    assert spotify.spotify_get("/foo") is None


def test_caches_successful_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify, "_ensure_fresh_access_token", lambda **_k: "AT")
    call_count = [0]

    def mock_get(url: str, headers: dict | None = None) -> MagicMock:
        call_count[0] += 1
        return _mock_response(200, {"x": 1})

    monkeypatch.setattr(spotify, "http_get", mock_get)
    spotify.spotify_get("/cached")
    spotify.spotify_get("/cached")
    assert call_count[0] == 1


def test_retries_on_401_with_forced_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify, "is_authenticated", lambda: True)
    tokens = ["AT_OLD", "AT_NEW"]

    def fake_ensure(force_refresh: bool = False) -> str:
        return tokens.pop(0) if tokens else ""

    monkeypatch.setattr(spotify, "_ensure_fresh_access_token", fake_ensure)
    responses = [_mock_response(401, None), _mock_response(200, {"ok": True})]
    monkeypatch.setattr(
        spotify, "http_get", lambda url, headers=None: responses.pop(0)
    )
    assert spotify.spotify_get("/protected") == {"ok": True}


def test_no_retry_when_no_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """If we have no refresh_token, do not attempt the 401 retry."""
    monkeypatch.setattr(spotify, "_ensure_fresh_access_token", lambda **_k: "AT")
    monkeypatch.setattr(spotify, "is_authenticated", lambda: False)
    call_count = [0]

    def mock_get(url: str, headers: dict | None = None) -> MagicMock:
        call_count[0] += 1
        return _mock_response(401, None)

    monkeypatch.setattr(spotify, "http_get", mock_get)
    assert spotify.spotify_get("/protected") is None
    assert call_count[0] == 1


def test_circuit_breaker_after_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify, "_ensure_fresh_access_token", lambda **_k: "AT")
    monkeypatch.setattr(spotify, "is_authenticated", lambda: False)
    monkeypatch.setattr(
        spotify, "http_get", lambda url, headers=None: _mock_response(500, None)
    )
    for idx in range(spotify._CIRCUIT_BREAKER_THRESHOLD):
        spotify.spotify_get(f"/url_{idx}")
    assert spotify._consecutive_failures_sp >= spotify._CIRCUIT_BREAKER_THRESHOLD

    call_count = [0]

    def short_circuit_get(url: str, headers: dict | None = None) -> MagicMock:
        call_count[0] += 1
        return _mock_response(200, {})

    monkeypatch.setattr(spotify, "http_get", short_circuit_get)
    assert spotify.spotify_get("/blocked") is None
    assert call_count[0] == 0


def test_cache_lru_eviction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify, "_CACHE_MAX_SIZE", 3)
    monkeypatch.setattr(spotify, "_ensure_fresh_access_token", lambda **_k: "AT")
    monkeypatch.setattr(
        spotify,
        "http_get",
        lambda url, headers=None: _mock_response(200, {"u": url}),
    )
    spotify.spotify_get("/a")
    spotify.spotify_get("/b")
    spotify.spotify_get("/c")
    spotify.spotify_get("/d")
    assert "/a" not in spotify._SPOTIFY_CACHE
    assert "/d" in spotify._SPOTIFY_CACHE


def test_clear_api_cache_empties_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify, "_ensure_fresh_access_token", lambda **_k: "AT")
    monkeypatch.setattr(
        spotify, "http_get", lambda url, headers=None: _mock_response(200, {"v": 1})
    )
    spotify.spotify_get("/x")
    assert spotify._SPOTIFY_CACHE
    spotify.clear_api_cache()
    assert spotify._SPOTIFY_CACHE == {}


def test_connection_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests as _requests  # noqa: PLC0415

    monkeypatch.setattr(spotify, "_ensure_fresh_access_token", lambda **_k: "AT")
    monkeypatch.setattr(spotify, "is_authenticated", lambda: False)

    def boom(url: str, headers: dict | None = None) -> MagicMock:
        raise _requests.ConnectionError("nope")

    monkeypatch.setattr(spotify, "http_get", boom)
    assert spotify.spotify_get("/x") is None
