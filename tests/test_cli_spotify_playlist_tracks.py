"""Tests for music_manager.cli.spotify_playlist_tracks."""

import json

import pytest

from music_manager.cli import spotify_playlist_tracks


def _payload(name: str = "X", tracks: list | None = None, skipped: int = 0) -> dict:
    return {
        "name": name,
        "creator": "thomas",
        "nb_tracks": 2,
        "cover_url": "https://c.jpg",
        "tracks": tracks
        or [
            {
                "isrc": "USX111",
                "title": "Bad Guy",
                "artist": "Billie",
                "cover_url": "https://i.jpg",
                "preview_url": "https://p.mp3",
            },
            {
                "isrc": "USX222",
                "title": "Other",
                "artist": "Else",
                "cover_url": "https://i2.jpg",
                "preview_url": "",
            },
        ],
        "skipped_no_isrc": skipped,
    }


def test_outputs_playlist_shape(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_playlist_tracks, "load_config", lambda: {"data_root": ""})
    monkeypatch.setattr(
        spotify_playlist_tracks,
        "fetch_spotify_playlist_preview",
        lambda pid, max_tracks=500: _payload(),
    )
    exit_code = spotify_playlist_tracks.main(["PLAYLIST_ABC"])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "X"
    assert len(out["tracks"]) == 2
    assert out["tracks"][0]["in_library"] is False
    assert out["tracks"][0]["apple_id"] == ""


def test_liked_id_dispatches_to_fetch_liked(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_playlist_tracks, "load_config", lambda: {"data_root": ""})
    called_with: list[int] = []

    def fake_liked(max_tracks: int = 500) -> dict:
        called_with.append(max_tracks)
        return _payload(name="♥ Titres likés")

    monkeypatch.setattr(spotify_playlist_tracks, "fetch_liked_tracks", fake_liked)

    def must_not_be_called(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("playlist preview must not be called for 'liked'")

    monkeypatch.setattr(
        spotify_playlist_tracks,
        "fetch_spotify_playlist_preview",
        must_not_be_called,
    )
    spotify_playlist_tracks.main(["liked"])
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "♥ Titres likés"
    assert called_with == [500]


def test_enriches_in_library_from_tracks_json(
    tmp_path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "music"
    (data_root / ".data").mkdir(parents=True)
    (data_root / ".data" / "tracks.json").write_text(
        '{"AP_BAD_GUY": {"isrc": "USX111", "title": "Bad Guy", "apple_id": "AP_BAD_GUY"}}'
    )
    monkeypatch.setattr(
        spotify_playlist_tracks,
        "load_config",
        lambda: {"data_root": str(data_root)},
    )
    monkeypatch.setattr(
        spotify_playlist_tracks,
        "fetch_spotify_playlist_preview",
        lambda pid, max_tracks=500: _payload(),
    )
    monkeypatch.setattr(spotify_playlist_tracks, "apple_ids_exist", lambda ids: {"AP_BAD_GUY"})
    spotify_playlist_tracks.main(["ABC"])
    out = json.loads(capsys.readouterr().out)
    assert out["tracks"][0]["in_library"] is True
    assert out["tracks"][0]["apple_id"] == "AP_BAD_GUY"
    assert out["tracks"][1]["in_library"] is False
    assert out["tracks"][1]["apple_id"] == ""


def test_drops_orphaned_apple_id(
    tmp_path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "music"
    (data_root / ".data").mkdir(parents=True)
    (data_root / ".data" / "tracks.json").write_text(
        '{"AP_DEAD": {"isrc": "USX111", "apple_id": "AP_DEAD"}}'
    )
    monkeypatch.setattr(
        spotify_playlist_tracks,
        "load_config",
        lambda: {"data_root": str(data_root)},
    )
    monkeypatch.setattr(
        spotify_playlist_tracks,
        "fetch_spotify_playlist_preview",
        lambda pid, max_tracks=500: _payload(),
    )
    monkeypatch.setattr(spotify_playlist_tracks, "apple_ids_exist", lambda ids: set())
    spotify_playlist_tracks.main(["ABC"])
    out = json.loads(capsys.readouterr().out)
    assert out["tracks"][0]["in_library"] is False
    assert out["tracks"][0]["apple_id"] == ""


def test_max_flag_forwarded(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spotify_playlist_tracks, "load_config", lambda: {"data_root": ""})
    captured: list[int] = []

    def fake_preview(pid: str, max_tracks: int = 500) -> dict:
        captured.append(max_tracks)
        return _payload()

    monkeypatch.setattr(spotify_playlist_tracks, "fetch_spotify_playlist_preview", fake_preview)
    spotify_playlist_tracks.main(["ABC", "--max", "120"])
    assert captured == [120]


def test_error_response(capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify_playlist_tracks, "load_config", lambda: {"data_root": ""})

    def raise_err(pid: str, max_tracks: int = 500) -> dict:
        raise RuntimeError("token expired")

    monkeypatch.setattr(spotify_playlist_tracks, "fetch_spotify_playlist_preview", raise_err)
    exit_code = spotify_playlist_tracks.main(["ABC"])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert "token expired" in out["error"]
