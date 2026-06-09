"""Tests for Spotify token persistence (save/load/clear) + chmod 600."""

import json
import os
import stat
import time

import pytest

from music_manager.services import spotify


def test_save_tokens_writes_to_config(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path
    config_path = config_dir / "config.json"
    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(config_dir))
    monkeypatch.setattr("music_manager.core.config.CONFIG_PATH", str(config_path))

    before = time.time()
    spotify.save_tokens("AT_value", "RT_value", 3600)
    after = time.time()

    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert data["spotify_access_token"] == "AT_value"
    assert data["spotify_refresh_token"] == "RT_value"
    assert before + 3500 <= data["spotify_token_expiry"] <= after + 3700


def test_save_tokens_sets_mode_600(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path
    config_path = config_dir / "config.json"
    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(config_dir))
    monkeypatch.setattr("music_manager.core.config.CONFIG_PATH", str(config_path))

    spotify.save_tokens("AT", "RT", 3600)
    mode = stat.S_IMODE(os.stat(config_path).st_mode)
    assert mode == 0o600


def test_load_tokens_returns_persisted_values(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path
    config_path = config_dir / "config.json"
    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(config_dir))
    monkeypatch.setattr("music_manager.core.config.CONFIG_PATH", str(config_path))

    spotify.save_tokens("MY_AT", "MY_RT", 1800)
    tokens = spotify.load_tokens()
    assert tokens["access_token"] == "MY_AT"
    assert tokens["refresh_token"] == "MY_RT"
    assert tokens["expiry"] > time.time()


def test_load_tokens_empty_when_no_config(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "music_manager.core.config.CONFIG_PATH", str(tmp_path / "config.json")
    )
    tokens = spotify.load_tokens()
    assert tokens["access_token"] == ""
    assert tokens["refresh_token"] == ""
    assert tokens["expiry"] == 0.0


def test_clear_tokens_resets_config(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path
    config_path = config_dir / "config.json"
    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(config_dir))
    monkeypatch.setattr("music_manager.core.config.CONFIG_PATH", str(config_path))

    spotify.save_tokens("AT", "RT", 3600)
    assert spotify.load_tokens()["refresh_token"] == "RT"
    spotify.clear_tokens()
    tokens = spotify.load_tokens()
    assert tokens["access_token"] == ""
    assert tokens["refresh_token"] == ""
    assert tokens["expiry"] == 0.0


def test_is_authenticated_true_when_refresh_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "load_tokens",
        lambda: {"refresh_token": "RT", "access_token": "", "expiry": 0.0},
    )
    assert spotify.is_authenticated() is True


def test_is_authenticated_false_when_no_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify,
        "load_tokens",
        lambda: {"refresh_token": "", "access_token": "AT", "expiry": 999999.0},
    )
    assert spotify.is_authenticated() is False
