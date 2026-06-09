"""Tests for music_manager.cli.spotify_auth_status."""

import json
import time

import pytest

from music_manager.cli import spotify_auth_status


def test_authenticated_true(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    expiry = time.time() + 1000
    monkeypatch.setattr(
        spotify_auth_status,
        "load_tokens",
        lambda: {"refresh_token": "RT", "access_token": "AT", "expiry": expiry},
    )
    monkeypatch.setattr(spotify_auth_status, "get_client_id", lambda: "CID")
    spotify_auth_status.main([])
    out = json.loads(capsys.readouterr().out)
    assert out["authenticated"] is True
    assert 900 <= out["expires_in"] <= 1000
    assert out["client_id_set"] is True


def test_authenticated_false_when_no_refresh(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        spotify_auth_status,
        "load_tokens",
        lambda: {"refresh_token": "", "access_token": "", "expiry": 0.0},
    )
    monkeypatch.setattr(spotify_auth_status, "get_client_id", lambda: "CID")
    spotify_auth_status.main([])
    out = json.loads(capsys.readouterr().out)
    assert out["authenticated"] is False
    assert out["expires_in"] == 0
    assert out["client_id_set"] is True


def test_expired_token_still_reports_authenticated(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live refresh_token means authenticated, even if access is expired."""
    monkeypatch.setattr(
        spotify_auth_status,
        "load_tokens",
        lambda: {
            "refresh_token": "RT",
            "access_token": "AT_OLD",
            "expiry": time.time() - 500,
        },
    )
    monkeypatch.setattr(spotify_auth_status, "get_client_id", lambda: "CID")
    spotify_auth_status.main([])
    out = json.loads(capsys.readouterr().out)
    assert out["authenticated"] is True
    assert out["expires_in"] == 0


def test_client_id_set_false_when_missing(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        spotify_auth_status,
        "load_tokens",
        lambda: {"refresh_token": "", "access_token": "", "expiry": 0.0},
    )
    monkeypatch.setattr(spotify_auth_status, "get_client_id", lambda: "")
    spotify_auth_status.main([])
    out = json.loads(capsys.readouterr().out)
    assert out["client_id_set"] is False
