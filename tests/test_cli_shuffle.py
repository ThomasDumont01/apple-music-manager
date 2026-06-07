"""Tests for music_manager/cli/shuffle.py."""

import json
from unittest.mock import patch

import pytest

from music_manager.cli import dispatch, shuffle


def test_shuffle_enables_shuffle_and_plays_library(
    capsys: pytest.CaptureFixture,
) -> None:
    """Activates Music, enables shuffle, plays library playlist 1."""
    with patch(
        "music_manager.cli.shuffle.run_applescript", return_value=""
    ) as mock_script:
        code = shuffle.main([])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok"}
    script = mock_script.call_args[0][0]
    assert "activate" in script
    assert "set shuffle enabled to true" in script
    assert "play library playlist 1" in script
    # Recentering block must be present (window settle UX).
    assert "bounds of window of desktop" in script


def test_shuffle_handles_applescript_failure(capsys: pytest.CaptureFixture) -> None:
    """A None return from run_applescript is reported as JSON error."""
    with patch("music_manager.cli.shuffle.run_applescript", return_value=None):
        shuffle.main([])
    assert json.loads(capsys.readouterr().out) == {"error": "applescript_failed"}


def test_dispatcher_routes_to_shuffle() -> None:
    with patch("music_manager.cli.shuffle.main", return_value=0) as mock_main:
        code = dispatch(["shuffle"])
    mock_main.assert_called_once_with([])
    assert code == 0
