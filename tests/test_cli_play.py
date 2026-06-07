"""Tests for music_manager/cli/play.py."""

import json
from unittest.mock import patch

import pytest

from music_manager.cli import dispatch, play


@pytest.fixture(autouse=True)
def _silent_music_launch(monkeypatch):
    """Tests never actually launch Music — mock the open + sleep helpers."""
    monkeypatch.setattr(
        "music_manager.cli.play._music_was_running", lambda: True
    )
    monkeypatch.setattr(
        "music_manager.cli.play.subprocess.run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )
    monkeypatch.setattr("music_manager.cli.play.time.sleep", lambda _s: None)


def test_play_invokes_applescript_with_apple_id(capsys: pytest.CaptureFixture) -> None:
    """A valid apple_id is forwarded to AppleScript: reveal album view + play."""
    with patch(
        "music_manager.cli.play.run_applescript", return_value=""
    ) as mock_script:
        code = play.main(["9878CAFBC2B2BB75"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "ok"}
    mock_script.assert_called_once()
    script_arg = mock_script.call_args[0][0]
    assert "9878CAFBC2B2BB75" in script_arg
    # No `activate` — Music stays hidden. `open -gj` (subprocess) does the
    # launch, AppleScript only plays.
    assert "activate" not in script_arg
    assert "play t" in script_arg


def test_play_normalizes_lowercase_input(capsys: pytest.CaptureFixture) -> None:
    """Apple persistent IDs canonicalize to uppercase before validation."""
    with patch(
        "music_manager.cli.play.run_applescript", return_value=""
    ) as mock_script:
        play.main(["9878cafbc2b2bb75"])
    assert "9878CAFBC2B2BB75" in mock_script.call_args[0][0]


def test_play_rejects_invalid_id_format(capsys: pytest.CaptureFixture) -> None:
    """Anti-injection: anything that isn't 16 hex chars is rejected."""
    with patch("music_manager.cli.play.run_applescript") as mock_script:
        code = play.main(["NOTHEX0000000000"])
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"error": "invalid_apple_id"}
    mock_script.assert_not_called()
    assert code == 0  # exit 0 because we conveyed via JSON


def test_play_rejects_too_short(capsys: pytest.CaptureFixture) -> None:
    with patch("music_manager.cli.play.run_applescript") as mock_script:
        play.main(["ABC"])
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_apple_id"}
    mock_script.assert_not_called()


def test_play_rejects_shell_injection_attempt(capsys: pytest.CaptureFixture) -> None:
    """Special characters never reach AppleScript."""
    with patch("music_manager.cli.play.run_applescript") as mock_script:
        play.main(['"; do evil; "'])
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_apple_id"}
    mock_script.assert_not_called()


def test_play_reports_applescript_failure(capsys: pytest.CaptureFixture) -> None:
    """run_applescript returns None on failure → JSON error, no crash."""
    with patch("music_manager.cli.play.run_applescript", return_value=None):
        code = play.main(["9878CAFBC2B2BB75"])
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"error": "applescript_failed"}
    assert code == 0


def test_dispatcher_routes_to_play() -> None:
    """The dispatcher routes 'play' to the right module."""
    with patch("music_manager.cli.play.main", return_value=0) as mock_main:
        code = dispatch(["play", "9878CAFBC2B2BB75"])
    mock_main.assert_called_once_with(["9878CAFBC2B2BB75"])
    assert code == 0
