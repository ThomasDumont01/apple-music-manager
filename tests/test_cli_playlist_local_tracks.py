"""Tests for music_manager.cli.playlist_local_tracks."""

import json

import pytest

from music_manager.cli import playlist_local_tracks


def test_lists_playlist_tracks(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "music"
    (data_root / ".data").mkdir(parents=True)
    (data_root / ".data" / "tracks.json").write_text(
        '{"AP_BG": {"isrc": "USX111", "title": "Bad Guy", '
        '"artist": "Billie", "cover_url": "https://c1.jpg"},'
        '"AP_OT": {"isrc": "USX222", "title": "Other", '
        '"artist": "Else", "cover_url": "https://c2.jpg"}}'
    )
    monkeypatch.setattr(
        playlist_local_tracks,
        "load_config",
        lambda: {"data_root": str(data_root)},
    )
    monkeypatch.setattr(
        playlist_local_tracks,
        "_load_playlist_items",
        lambda name: [
            {"apple_id": "AP_BG", "title": "Bad Guy", "artist": "Billie"},
            {"apple_id": "AP_OT", "title": "Other", "artist": "Else"},
        ],
    )
    exit_code = playlist_local_tracks.main(["like"])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "like"
    assert len(out["tracks"]) == 2
    assert out["tracks"][0]["apple_id"] == "AP_BG"
    assert out["tracks"][0]["isrc"] == "USX111"
    assert out["tracks"][0]["cover_url"] == "https://c1.jpg"
    assert out["tracks"][0]["in_library"] is True


def test_handles_tracks_without_tracks_json(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "music"
    monkeypatch.setattr(
        playlist_local_tracks,
        "load_config",
        lambda: {"data_root": str(data_root)},
    )
    monkeypatch.setattr(
        playlist_local_tracks,
        "_load_playlist_items",
        lambda name: [
            {"apple_id": "AP_X", "title": "Unknown", "artist": "Anon"},
        ],
    )
    playlist_local_tracks.main(["MyList"])
    out = json.loads(capsys.readouterr().out)
    assert out["tracks"][0]["apple_id"] == "AP_X"
    assert out["tracks"][0]["title"] == "Unknown"
    assert out["tracks"][0]["isrc"] == ""
    assert out["tracks"][0]["cover_url"] == ""
    assert out["tracks"][0]["in_library"] is True


def test_not_found(capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(playlist_local_tracks, "load_config", lambda: {"data_root": ""})
    monkeypatch.setattr(playlist_local_tracks, "_load_playlist_items", lambda name: None)
    exit_code = playlist_local_tracks.main(["nope"])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "not_found"


def test_empty_playlist(capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(playlist_local_tracks, "load_config", lambda: {"data_root": ""})
    monkeypatch.setattr(playlist_local_tracks, "_load_playlist_items", lambda name: [])
    exit_code = playlist_local_tracks.main(["empty"])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["tracks"] == []
    assert out["nb_tracks"] == 0


def test_apple_id_uppercased(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apple persistent IDs are 16-char uppercase hex; tracks.json keys match."""
    data_root = tmp_path / "music"
    (data_root / ".data").mkdir(parents=True)
    (data_root / ".data" / "tracks.json").write_text('{"ABCDEF0123456789": {"isrc": "USX1"}}')
    monkeypatch.setattr(
        playlist_local_tracks,
        "load_config",
        lambda: {"data_root": str(data_root)},
    )
    monkeypatch.setattr(
        playlist_local_tracks,
        "_load_playlist_items",
        lambda name: [{"apple_id": "ABCDEF0123456789", "title": "T", "artist": "A"}],
    )
    playlist_local_tracks.main(["x"])
    out = json.loads(capsys.readouterr().out)
    assert out["tracks"][0]["isrc"] == "USX1"


def test_isrc_normalised_uppercase(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "music"
    (data_root / ".data").mkdir(parents=True)
    (data_root / ".data" / "tracks.json").write_text('{"AP_X": {"isrc": "usx_lower"}}')
    monkeypatch.setattr(
        playlist_local_tracks,
        "load_config",
        lambda: {"data_root": str(data_root)},
    )
    monkeypatch.setattr(
        playlist_local_tracks,
        "_load_playlist_items",
        lambda name: [{"apple_id": "AP_X", "title": "T", "artist": "A"}],
    )
    playlist_local_tracks.main(["x"])
    out = json.loads(capsys.readouterr().out)
    assert out["tracks"][0]["isrc"] == "USX_LOWER"
