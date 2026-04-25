"""Smoke tests for __main__.py — verify the app starts on a fresh machine.

Tests the full entry point path with all external services mocked.
Proves the app doesn't crash on first launch or subsequent launches.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from music_manager.__main__ import _convert_all_exportify, main
from music_manager.core.io import save_csv

_P_MAIN = "music_manager.__main__"
_P_CONFIG = "music_manager.core.config"


# ── Smoke: first launch (setup_done=False) ─────────────────────────────────


@patch("music_manager.ui.app.MusicApp")
@patch(f"{_P_MAIN}.Apple")
@patch("music_manager.services.resolver.configure")
@patch(f"{_P_MAIN}.init_logger")
@patch(f"{_P_MAIN}.create_data_folders")
@patch(f"{_P_MAIN}.save_config")
@patch(f"{_P_MAIN}.choose_data_root")
@patch(f"{_P_MAIN}.load_config")
@patch(f"{_P_MAIN}.check_macos", return_value=True)
def test_first_launch_creates_app(
    mock_macos,
    mock_load,
    mock_choose,
    mock_save,
    mock_folders,
    mock_logger,
    mock_resolver,
    mock_apple,
    mock_app_cls,
    tmp_path: Path,
) -> None:
    """First launch: choose root → create folders → launch app (setup_done=False)."""
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    mock_load.return_value = {"data_root": "", "setup_done": False}
    mock_choose.return_value = data_root

    mock_app = MagicMock()
    mock_app_cls.return_value = mock_app

    main()

    mock_choose.assert_called_once()
    mock_save.assert_called_once()
    mock_folders.assert_called_once_with(data_root)
    mock_app.run.assert_called_once()

    # Verify app was created with setup_done=False
    call_kwargs = mock_app_cls.call_args
    assert call_kwargs.kwargs["setup_done"] is False
    assert call_kwargs.kwargs["tracks_store"] is None
    assert call_kwargs.kwargs["albums_store"] is None


# ── Smoke: subsequent launch (setup_done=True) ────────────────────────────


@patch("music_manager.ui.app.MusicApp")
@patch(f"{_P_MAIN}.Apple")
@patch(f"{_P_MAIN}.Tracks")
@patch(f"{_P_MAIN}.Albums")
@patch("music_manager.services.resolver.configure")
@patch(f"{_P_MAIN}.init_logger")
@patch(f"{_P_MAIN}.create_data_folders")
@patch(f"{_P_MAIN}.load_config")
@patch(f"{_P_MAIN}.check_macos", return_value=True)
def test_subsequent_launch_loads_stores(
    mock_macos,
    mock_load,
    mock_folders,
    mock_logger,
    mock_resolver,
    mock_albums,
    mock_tracks,
    mock_apple,
    mock_app_cls,
    tmp_path: Path,
) -> None:
    """Subsequent launch: loads tracks + albums stores, passes setup_done=True."""
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    mock_load.return_value = {"data_root": data_root, "setup_done": True}

    mock_app = MagicMock()
    mock_app_cls.return_value = mock_app

    main()

    # Stores should be loaded (not None)
    mock_tracks.assert_called_once()
    mock_albums.assert_called_once()
    mock_app.run.assert_called_once()
    assert mock_app_cls.call_args.kwargs["setup_done"] is True


# ── Smoke: user cancels data root picker ───────────────────────────────────


@patch(f"{_P_MAIN}.choose_data_root", return_value=None)
@patch(f"{_P_MAIN}.load_config", return_value={"data_root": "", "setup_done": False})
@patch(f"{_P_MAIN}.check_macos", return_value=True)
def test_user_cancels_root_exits_cleanly(mock_macos, mock_load, mock_choose) -> None:
    """User cancels data root picker → sys.exit(0), no crash."""
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0


# ── Smoke: non-macOS → sys.exit with French message ───────────────────────


@patch(f"{_P_MAIN}.check_macos", return_value=False)
def test_non_macos_exits_with_message(mock_macos) -> None:
    """Non-macOS → exit with French error message."""
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert "macOS" in str(exc_info.value)


# ── Smoke: create_data_folders fails → clean error ────────────────────────


@patch(f"{_P_MAIN}.create_data_folders", side_effect=OSError("Permission denied"))
@patch(f"{_P_MAIN}.load_config", return_value={"data_root": "/tmp/test_mm", "setup_done": False})
@patch(f"{_P_MAIN}.check_macos", return_value=True)
def test_disk_full_exits_with_french_error(mock_macos, mock_load, mock_folders) -> None:
    """Disk full / permissions → exit with French error message."""
    os.makedirs("/tmp/test_mm", exist_ok=True)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert "Impossible" in str(exc_info.value)


# ── Smoke: app.run() crash → logged + clean exit ─────────────────────────


@patch("music_manager.ui.app.MusicApp")
@patch(f"{_P_MAIN}.Apple")
@patch("music_manager.services.resolver.configure")
@patch(f"{_P_MAIN}.init_logger")
@patch(f"{_P_MAIN}.create_data_folders")
@patch(f"{_P_MAIN}.load_config")
@patch(f"{_P_MAIN}.check_macos", return_value=True)
def test_app_crash_logs_and_exits(
    mock_macos,
    mock_load,
    mock_folders,
    mock_logger,
    mock_resolver,
    mock_apple,
    mock_app_cls,
    tmp_path: Path,
) -> None:
    """App crash during run → logged as 'crash' event + French exit message."""
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    mock_load.return_value = {"data_root": data_root, "setup_done": False}

    mock_app = MagicMock()
    mock_app.run.side_effect = RuntimeError("Segfault in Textual")
    mock_app_cls.return_value = mock_app

    with patch("music_manager.core.logger.log_event") as mock_log:
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert "Erreur fatale" in str(exc_info.value)
        # session_start, crash, session_end all logged
        crash_calls = [c for c in mock_log.call_args_list if c[0][0] == "crash"]
        assert len(crash_calls) == 1
        assert crash_calls[0] == call("crash", error="Segfault in Textual")
        # session_end always logged in finally block
        end_calls = [c for c in mock_log.call_args_list if c[0][0] == "session_end"]
        assert len(end_calls) == 1
        assert "duration_ms" in end_calls[0][1]


# ── Exportify conversion ──────────────────────────────────────────────────


def test_convert_exportify_handles_empty_dir(tmp_path: Path) -> None:
    """Empty directory → no crash, no conversion."""
    _convert_all_exportify(str(tmp_path / "nonexistent.csv"), str(tmp_path / "nodir"))
    # No crash = success


def test_convert_exportify_converts_csvs(tmp_path: Path) -> None:
    """CSVs in directory are processed without crash."""
    playlist_dir = tmp_path / "playlists"
    playlist_dir.mkdir()
    csv_path = playlist_dir / "test.csv"
    save_csv(str(csv_path), [{"title": "Song", "artist": "Art", "album": "Al"}])

    _convert_all_exportify("", str(playlist_dir))
    # No crash, CSV still readable
    from music_manager.core.io import load_csv  # noqa: PLC0415

    rows = load_csv(str(csv_path))
    assert len(rows) == 1


# ── Smoke: KeyboardInterrupt → silent exit ────────────────────────────────


@patch("music_manager.ui.app.MusicApp")
@patch(f"{_P_MAIN}.Apple")
@patch("music_manager.services.resolver.configure")
@patch(f"{_P_MAIN}.init_logger")
@patch(f"{_P_MAIN}.create_data_folders")
@patch(f"{_P_MAIN}.load_config")
@patch(f"{_P_MAIN}.check_macos", return_value=True)
def test_keyboard_interrupt_silent_exit(
    mock_macos,
    mock_load,
    mock_folders,
    mock_logger,
    mock_resolver,
    mock_apple,
    mock_app_cls,
    tmp_path: Path,
) -> None:
    """Ctrl+C → no crash, no error message, clean exit."""
    data_root = str(tmp_path / "data")
    os.makedirs(data_root, exist_ok=True)
    mock_load.return_value = {"data_root": data_root, "setup_done": False}

    mock_app = MagicMock()
    mock_app.run.side_effect = KeyboardInterrupt()
    mock_app_cls.return_value = mock_app

    # Should NOT raise SystemExit — just return silently
    main()
