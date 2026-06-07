"""Tests for music_manager/cli/play_playlist.py."""

import json
from unittest.mock import patch

import pytest

from music_manager.cli import dispatch, play_playlist


@pytest.fixture(autouse=True)
def _silent_music_launch(monkeypatch):
    """Tests never actually launch Music — mock the open + sleep helpers."""
    monkeypatch.setattr(
        "music_manager.cli.play_playlist._music_was_running", lambda: True
    )
    monkeypatch.setattr(
        "music_manager.cli.play_playlist.subprocess.run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )
    monkeypatch.setattr(
        "music_manager.cli.play_playlist.time.sleep", lambda _s: None
    )


def test_play_playlist_runs_applescript(capsys: pytest.CaptureFixture) -> None:
    """A valid playlist name triggers reveal + play via AppleScript."""
    with patch(
        "music_manager.cli.play_playlist.run_applescript", return_value=""
    ) as mock_script:
        code = play_playlist.main(["Chill"])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok"}
    script = mock_script.call_args[0][0]
    assert 'user playlist "Chill"' in script
    # La playlist est bindée à `pl` puis jouée via `play pl`. Pas
    # d'`activate` : Music a été lancé caché via `open -gj` (subprocess).
    assert "play pl" in script
    assert "activate" not in script


def test_play_playlist_escapes_special_chars(capsys: pytest.CaptureFixture) -> None:
    """Double quotes in playlist names are escaped — anti-injection."""
    with patch(
        "music_manager.cli.play_playlist.run_applescript", return_value=""
    ) as mock_script:
        play_playlist.main(['Mood "evening"'])
    script = mock_script.call_args[0][0]
    # Double-quoted name must be escaped (no naked `"`).
    assert 'Mood \\"evening\\"' in script


def test_play_playlist_rejects_empty_name(capsys: pytest.CaptureFixture) -> None:
    with patch("music_manager.cli.play_playlist.run_applescript") as mock_script:
        play_playlist.main(["   "])
    assert json.loads(capsys.readouterr().out) == {"error": "empty_name"}
    mock_script.assert_not_called()


def test_play_playlist_handles_applescript_failure(
    capsys: pytest.CaptureFixture,
) -> None:
    with patch("music_manager.cli.play_playlist.run_applescript", return_value=None):
        play_playlist.main(["Chill"])
    assert json.loads(capsys.readouterr().out) == {"error": "applescript_failed"}


def test_dispatcher_routes_to_play_playlist() -> None:
    with patch(
        "music_manager.cli.play_playlist.main", return_value=0
    ) as mock_main:
        code = dispatch(["play-playlist", "Chill"])
    mock_main.assert_called_once_with(["Chill"])
    assert code == 0
