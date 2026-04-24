"""Tests for core/checks.py."""

from unittest.mock import patch

from music_manager.core.checks import check_dependencies


def test_check_dependencies_all_present() -> None:
    """No missing deps when afplay/yt-dlp/ffmpeg are installed."""
    missing = check_dependencies()
    # afplay is always present on macOS
    assert "afplay" not in missing


def test_check_dependencies_detects_missing() -> None:
    """Detects a fake missing dependency."""
    with patch("music_manager.core.checks.shutil.which", return_value=None):
        missing = check_dependencies()
    assert "afplay" in missing
    assert "yt-dlp" in missing
    assert "ffmpeg" in missing
