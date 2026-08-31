"""Tests for options/maintenance.py — reset, clear, revert operations."""

from pathlib import Path

import pytest

from music_manager.options.maintenance import (
    delete_all,
    reset_failed,
)
from music_manager.services.tracks import Tracks

_PATCH = "music_manager.options.maintenance"


# ── reset_failed ────────────────────────────────────────────────────────────


def test_reset_failed_clears_status(tmp_path: Path) -> None:
    """Failed tracks get status=None and fail_reason cleared."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Song 1", "status": "failed", "fail_reason": "timeout"})
    tracks.add("A2", {"title": "Song 2", "status": "failed", "fail_reason": "network"})
    tracks.add("A3", {"title": "Song 3", "status": "done"})

    count = reset_failed(tracks)

    assert count == 2
    entry_a1 = tracks.get_by_apple_id("A1")
    assert entry_a1 is not None
    assert entry_a1["status"] is None
    assert entry_a1["fail_reason"] == ""
    entry_a3 = tracks.get_by_apple_id("A3")
    assert entry_a3 is not None
    assert entry_a3["status"] == "done"


def test_reset_failed_nothing_to_reset(tmp_path: Path) -> None:
    """No failed tracks → returns 0."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Song", "status": "done"})

    count = reset_failed(tracks)
    assert count == 0


# ── delete_all ─────────────────────────────────────────────────────────────


def test_delete_all_removes_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deletes .data/ directory and config dir."""
    fake_config_dir = tmp_path / "config"
    fake_config_dir.mkdir()
    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(fake_config_dir))

    data_dir = tmp_path / ".data"
    data_dir.mkdir()
    (data_dir / "tracks.json").write_text("{}")
    (data_dir / "albums.json").write_text("{}")

    result = delete_all(str(tmp_path))

    assert result is True
    assert not data_dir.exists()
    assert not fake_config_dir.exists()


def test_delete_all_no_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No .data/ → returns False, no crash."""
    fake_config_dir = tmp_path / "config_empty"
    monkeypatch.setattr("music_manager.core.config.CONFIG_DIR", str(fake_config_dir))

    result = delete_all(str(tmp_path))
    assert result is False
