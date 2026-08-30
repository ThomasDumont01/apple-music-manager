"""Tests for core/config.py."""

import threading
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


# ── save_config — thread safety ──────────────────────────────────────────────


def test_save_config_concurrent_updates_keep_every_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parallel save_config() calls must not crash nor lose each other's keys.

    Regression: save_config() is a read-modify-write through a fixed
    ``config.json.tmp``. The recommendation feed builds its sections in
    parallel and each worker can refresh the Spotify token, so several
    threads hit this at once — one died on FileNotFoundError and the others
    silently overwrote each other's keys.
    """
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("music_manager.core.config.CONFIG_PATH", str(config_path))
    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(tmp_path))

    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def writer(index: int) -> None:
        barrier.wait()
        try:
            save_config({f"key_{index}": index})
        except BaseException as exc:  # noqa: BLE001 - test records everything
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == [], f"concurrent save_config raised: {errors[:3]}"
    final = load_config()
    missing = [f"key_{i}" for i in range(8) if final.get(f"key_{i}") != i]
    assert missing == [], f"lost updates: {missing}"
