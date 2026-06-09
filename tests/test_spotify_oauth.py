"""Tests for Spotify OAuth PKCE helpers + token exchange."""

import base64
import hashlib
from unittest.mock import patch

import pytest

from music_manager.services import spotify


def test_pkce_verifier_challenge_returns_pair() -> None:
    verifier, challenge = spotify.pkce_verifier_challenge()
    assert isinstance(verifier, str)
    assert len(verifier) >= 43
    assert isinstance(challenge, str)
    assert 43 <= len(challenge) <= 128


def test_pkce_challenge_is_sha256_of_verifier() -> None:
    verifier, challenge = spotify.pkce_verifier_challenge()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected


def test_pkce_no_padding_in_challenge() -> None:
    _, challenge = spotify.pkce_verifier_challenge()
    assert "=" not in challenge


def test_pkce_verifier_uses_url_safe_alphabet() -> None:
    verifier, _ = spotify.pkce_verifier_challenge()
    for ch in verifier:
        assert ch.isalnum() or ch in "-_"


def test_pkce_two_calls_distinct() -> None:
    v1, _ = spotify.pkce_verifier_challenge()
    v2, _ = spotify.pkce_verifier_challenge()
    assert v1 != v2


def test_build_auth_url_contains_required_params(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MM_SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.setattr(spotify, "_SPOTIFY_CLIENT_ID", "TEST_CLIENT_ID_XYZ")
    monkeypatch.setattr(spotify, "load_config", lambda: {})
    url = spotify.build_auth_url(state="ABCD1234", code_challenge="CHAL_XYZ")
    assert url.startswith("https://accounts.spotify.com/authorize?")
    assert "client_id=TEST_CLIENT_ID_XYZ" in url
    assert "response_type=code" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fcallback" in url
    assert "state=ABCD1234" in url
    assert "code_challenge_method=S256" in url
    assert "code_challenge=CHAL_XYZ" in url
    assert "playlist-read-private" in url
    assert "user-library-read" in url


def test_exchange_code_posts_correct_body() -> None:
    with patch("music_manager.services.spotify.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
        }
        out = spotify.exchange_code(code="AUTH_CODE", code_verifier="VERIFIER_XYZ")
    assert out["access_token"] == "AT"
    call = mock_post.call_args
    assert call.args[0] == spotify._SPOTIFY_TOKEN_URL
    payload = call.kwargs["data"]
    assert payload["grant_type"] == "authorization_code"
    assert payload["code"] == "AUTH_CODE"
    assert payload["code_verifier"] == "VERIFIER_XYZ"
    assert payload["redirect_uri"] == "http://127.0.0.1:8765/callback"
    assert "client_id" in payload


def test_exchange_code_raises_on_http_error() -> None:
    with patch("music_manager.services.spotify.requests.post") as mock_post:
        mock_post.return_value.status_code = 400
        with pytest.raises(RuntimeError, match="spotify_token_exchange_failed"):
            spotify.exchange_code(code="X", code_verifier="V")


def test_refresh_access_token_posts_correct_body() -> None:
    with patch("music_manager.services.spotify.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "access_token": "NEW_AT",
            "expires_in": 3600,
        }
        out = spotify.refresh_access_token("REFRESH_TOKEN_ABC")
    assert out["access_token"] == "NEW_AT"
    payload = mock_post.call_args.kwargs["data"]
    assert payload["grant_type"] == "refresh_token"
    assert payload["refresh_token"] == "REFRESH_TOKEN_ABC"
    assert "client_id" in payload


def test_refresh_access_token_raises_on_failure() -> None:
    with patch("music_manager.services.spotify.requests.post") as mock_post:
        mock_post.return_value.status_code = 400
        with pytest.raises(RuntimeError, match="spotify_refresh_failed"):
            spotify.refresh_access_token("RT")


def test_get_client_id_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MM_SPOTIFY_CLIENT_ID", "ENV_ID")
    monkeypatch.setattr(spotify, "_SPOTIFY_CLIENT_ID", "DEFAULT_ID")
    monkeypatch.setattr(spotify, "load_config", lambda: {"spotify_client_id": "CFG_ID"})
    assert spotify.get_client_id() == "ENV_ID"


def test_get_client_id_config_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MM_SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.setattr(spotify, "_SPOTIFY_CLIENT_ID", "DEFAULT_ID")
    monkeypatch.setattr(spotify, "load_config", lambda: {"spotify_client_id": "CFG_ID"})
    assert spotify.get_client_id() == "CFG_ID"


def test_get_client_id_falls_back_to_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MM_SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.setattr(spotify, "_SPOTIFY_CLIENT_ID", "DEFAULT_ID")
    monkeypatch.setattr(spotify, "load_config", lambda: {})
    assert spotify.get_client_id() == "DEFAULT_ID"
