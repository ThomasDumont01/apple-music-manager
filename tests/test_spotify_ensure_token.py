"""Tests for _ensure_fresh_access_token — auto-refresh logic."""

import time

import pytest

from music_manager.services import spotify


def test_returns_current_token_if_not_expiring_soon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "load_tokens",
        lambda: {
            "access_token": "AT",
            "refresh_token": "RT",
            "expiry": time.time() + 3600,
        },
    )
    assert spotify._ensure_fresh_access_token() == "AT"


def test_refreshes_when_within_60s(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "load_tokens",
        lambda: {
            "access_token": "AT_OLD",
            "refresh_token": "RT",
            "expiry": time.time() + 30,
        },
    )
    saved: dict[str, object] = {}

    def fake_save(access: str, refresh: str, expires_in: int) -> None:
        saved["access"] = access
        saved["refresh"] = refresh
        saved["expires_in"] = expires_in

    monkeypatch.setattr(spotify, "save_tokens", fake_save)
    monkeypatch.setattr(
        spotify,
        "refresh_access_token",
        lambda r: {"access_token": "AT_NEW", "expires_in": 3600},
    )
    assert spotify._ensure_fresh_access_token() == "AT_NEW"
    assert saved["access"] == "AT_NEW"
    assert saved["refresh"] == "RT"


def test_rotates_refresh_token_when_api_returns_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        spotify,
        "load_tokens",
        lambda: {"access_token": "", "refresh_token": "RT_OLD", "expiry": 0.0},
    )
    saved: dict[str, object] = {}
    monkeypatch.setattr(spotify, "save_tokens", lambda a, r, e: saved.update(refresh=r))
    monkeypatch.setattr(
        spotify,
        "refresh_access_token",
        lambda r: {
            "access_token": "AT_NEW",
            "refresh_token": "RT_NEW",
            "expires_in": 3600,
        },
    )
    spotify._ensure_fresh_access_token()
    assert saved["refresh"] == "RT_NEW"


def test_returns_empty_when_no_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "load_tokens",
        lambda: {"access_token": "", "refresh_token": "", "expiry": 0.0},
    )
    assert spotify._ensure_fresh_access_token() == ""


def test_returns_empty_when_refresh_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "load_tokens",
        lambda: {"access_token": "", "refresh_token": "RT", "expiry": 0.0},
    )

    def raise_err(_r: str) -> dict:
        raise RuntimeError("spotify_refresh_failed:400")

    monkeypatch.setattr(spotify, "refresh_access_token", raise_err)
    assert spotify._ensure_fresh_access_token() == ""


def test_force_refresh_skips_expiry_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "load_tokens",
        lambda: {
            "access_token": "AT",
            "refresh_token": "RT",
            "expiry": time.time() + 3600,
        },
    )
    saved: dict[str, object] = {}
    monkeypatch.setattr(spotify, "save_tokens", lambda a, r, e: saved.update(access=a))
    monkeypatch.setattr(
        spotify,
        "refresh_access_token",
        lambda r: {"access_token": "AT_FORCED", "expires_in": 3600},
    )
    assert spotify._ensure_fresh_access_token(force_refresh=True) == "AT_FORCED"
    assert saved["access"] == "AT_FORCED"
