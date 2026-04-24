"""Tests for core/config.py."""

from pathlib import Path

import pytest

from music_manager.core.config import Paths, load_config, save_config


def test_load_config_defaults_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No config file → returns defaults."""
    monkeypatch.setattr("music_manager.core.config.CONFIG_PATH", "/nonexistent/config.json")
    config = load_config()
    assert config["setup_done"] is False


def test_save_and_load_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Save then load returns same data."""
    monkeypatch.setattr("music_manager.core.config.CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(tmp_path))

    save_config({"data_root": "/test"})
    config = load_config()
    assert config["data_root"] == "/test"


def test_paths_french() -> None:
    """Paths uses French names."""
    paths = Paths("/root")
    assert paths.requests_path == "/root/requetes.csv"
    assert paths.shortcuts_dir == "/root/raccourcis"
    assert paths.tracks_path == "/root/.data/tracks.json"
