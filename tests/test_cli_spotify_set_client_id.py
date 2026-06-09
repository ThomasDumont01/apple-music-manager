"""Tests for music_manager.cli.spotify_set_client_id."""

import json

import pytest

from music_manager.cli import spotify_set_client_id


def test_valid_client_id_saves(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: dict[str, object] = {}
    monkeypatch.setattr(
        "music_manager.cli.spotify_set_client_id.save_config",
        saved.update,
    )
    exit_code = spotify_set_client_id.main(
        ["abcdef0123456789abcdef0123456789"]
    )
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"status": "ok"}
    assert saved["spotify_client_id"] == "abcdef0123456789abcdef0123456789"


def test_invalid_format_rejected(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: dict[str, object] = {}
    monkeypatch.setattr(
        "music_manager.cli.spotify_set_client_id.save_config",
        saved.update,
    )
    exit_code = spotify_set_client_id.main(["not-hex"])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "invalid_client_id_format"
    assert "spotify_client_id" not in saved


def test_uppercase_normalized_to_lowercase(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: dict[str, object] = {}
    monkeypatch.setattr(
        "music_manager.cli.spotify_set_client_id.save_config",
        saved.update,
    )
    spotify_set_client_id.main(["ABCDEF0123456789ABCDEF0123456789"])
    assert saved["spotify_client_id"] == "abcdef0123456789abcdef0123456789"


def test_whitespace_trimmed(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: dict[str, object] = {}
    monkeypatch.setattr(
        "music_manager.cli.spotify_set_client_id.save_config",
        saved.update,
    )
    spotify_set_client_id.main(["  abcdef0123456789abcdef0123456789  "])
    assert saved["spotify_client_id"] == "abcdef0123456789abcdef0123456789"


def test_too_short_rejected(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "music_manager.cli.spotify_set_client_id.save_config",
        lambda updates: None,
    )
    exit_code = spotify_set_client_id.main(["abc123"])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "invalid_client_id_format"


def test_too_long_rejected(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "music_manager.cli.spotify_set_client_id.save_config",
        lambda updates: None,
    )
    exit_code = spotify_set_client_id.main(["a" * 64])
    assert exit_code == 1


def test_non_hex_rejected(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "music_manager.cli.spotify_set_client_id.save_config",
        lambda updates: None,
    )
    exit_code = spotify_set_client_id.main(["zzzzzz0123456789abcdef0123456789"])
    assert exit_code == 1
