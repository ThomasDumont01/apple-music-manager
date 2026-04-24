"""Integration sanity check — verify this machine can run Music Manager."""

import pytest

from music_manager.core.checks import check_apple_music, check_dependencies
from music_manager.services.health import check_deezer, check_youtube

pytestmark = pytest.mark.integration


def test_dependencies_installed() -> None:
    """ffmpeg and yt-dlp are available."""
    missing = check_dependencies()
    assert not missing, f"Missing: {', '.join(missing)}"


def test_apple_music_responding() -> None:
    """Apple Music responds to AppleScript."""
    assert check_apple_music()


def test_deezer_reachable() -> None:
    """Deezer API is reachable."""
    assert check_deezer()


def test_youtube_reachable() -> None:
    """YouTube (yt-dlp) is functional."""
    assert check_youtube()
