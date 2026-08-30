"""The safety nets that keep the suite off the user's real machine.

These guards exist because both failure modes actually happened: a test once
created playlists in the real Apple Music library, and the startup sweep for
crash-orphaned locks once deleted the real ``~/.config/music_manager/.ui.lock``.
"""

import os

import pytest

from music_manager.core import config
from music_manager.core.config import Paths
from music_manager.services import apple


def test_config_dir_is_sandboxed() -> None:
    """No test may resolve to the user's real config directory."""
    real = os.path.join(os.path.expanduser("~"), ".config", "music_manager")
    assert config.CONFIG_DIR != real
    assert not config.CONFIG_PATH.startswith(real)


def test_widget_paths_follow_the_sandbox(tmp_path) -> None:
    """Lock/status/failure files land in the sandbox, not in the real config."""
    paths = Paths(str(tmp_path))
    for path in (
        paths.ui_lock_path,
        paths.widget_lock_path,
        paths.widget_status_path,
        paths.widget_failures_path,
        paths.youtube_state_path,
    ):
        assert path.startswith(config.CONFIG_DIR)


def test_osascript_is_blocked() -> None:
    """Both AppleScript entry points refuse to spawn a real osascript."""
    with pytest.raises(RuntimeError):
        apple.run_applescript('tell application "Music" to name')
    with pytest.raises(RuntimeError):
        apple.run_applescript_result('tell application "Music" to name')
