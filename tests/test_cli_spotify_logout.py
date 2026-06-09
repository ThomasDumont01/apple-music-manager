"""Tests for music_manager.cli.spotify_logout."""

import json

import pytest

from music_manager.cli import spotify_logout


def test_logout_clears_tokens(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(
        spotify_logout, "clear_tokens", lambda: called.append(True)
    )
    exit_code = spotify_logout.main([])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"status": "ok"}
    assert called == [True]
