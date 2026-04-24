"""Tests for options/maintenance.py — reset, clear, revert operations."""

from pathlib import Path
from unittest.mock import patch

import pytest

from music_manager.core.io import load_json, save_json
from music_manager.options.maintenance import (
    clear_preferences,
    delete_all,
    reset_failed,
    revert_imports,
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


# ── clear_preferences ──────────────────────────────────────────────────────


def test_clear_preferences(tmp_path: Path) -> None:
    """Preferences file is replaced with empty dict."""
    prefs_path = str(tmp_path / "prefs.json")
    save_json(prefs_path, {"refusals": {"A1:title": "val"}, "ignored_albums": ["Album"]})

    clear_preferences(prefs_path)

    prefs = load_json(prefs_path)
    assert prefs == {}


# ── revert_imports ─────────────────────────────────────────────────────────


@patch(f"{_PATCH}.delete_tracks")
def test_revert_imports_deletes_imported(mock_delete, tmp_path: Path) -> None:
    """Imported tracks are deleted from Apple Music and store."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Imported 1", "origin": "imported", "status": "done"})
    tracks.add("A2", {"title": "Imported 2", "origin": "imported", "status": "done"})
    tracks.add("A3", {"title": "Baseline", "origin": "baseline"})

    count = revert_imports(tracks)

    assert count == 2
    mock_delete.assert_called_once_with(["A1", "A2"])
    assert tracks.get_by_apple_id("A1") is None
    assert tracks.get_by_apple_id("A2") is None
    assert tracks.get_by_apple_id("A3") is not None


@patch(f"{_PATCH}.delete_tracks")
def test_revert_imports_nothing_to_revert(mock_delete, tmp_path: Path) -> None:
    """No imported tracks → returns 0, no delete call."""
    tracks = Tracks(str(tmp_path / "tracks.json"))
    tracks.add("A1", {"title": "Baseline", "origin": "baseline"})

    count = revert_imports(tracks)

    assert count == 0
    mock_delete.assert_not_called()


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
