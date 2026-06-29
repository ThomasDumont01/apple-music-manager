"""Tests for music_manager/cli/playlist_tracks.py."""

import json
from unittest.mock import patch

import pytest

from music_manager.cli import playlist_tracks


def _payload(
    name: str = "Chill",
    creator: str = "thomas",
    nb: int = 2,
    skipped: int = 0,
) -> dict:
    return {
        "name": name,
        "creator": creator,
        "nb_tracks": nb,
        "tracks": [
            {
                "isrc": "FRABC1234567",
                "title": "Bad Guy",
                "artist": "Billie Eilish",
                "cover_url": "https://e/a.jpg",
                "preview_url": "https://e/p1.mp3",
            },
            {
                "isrc": "USXYZ7654321",
                "title": "Other",
                "artist": "Someone",
                "cover_url": "https://e/b.jpg",
                "preview_url": "https://e/p2.mp3",
            },
        ],
        "skipped_no_isrc": skipped,
    }


def test_outputs_stable_json_schema(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload_in = _payload(skipped=1)
    monkeypatch.setattr(
        "music_manager.cli.playlist_tracks.load_config",
        lambda: {"data_root": ""},
    )
    with patch(
        "music_manager.cli.playlist_tracks.fetch_playlist_preview",
        return_value=payload_in,
    ):
        exit_code = playlist_tracks.main(["42"])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "Chill"
    # Verify the rich shape — widget relies on these keys.
    assert "tracks" in out
    assert out["tracks"][0]["title"] == "Bad Guy"
    assert out["tracks"][0]["artist"] == "Billie Eilish"
    assert out["tracks"][0]["cover_url"].startswith("https://")
    assert out["tracks"][0]["preview_url"].startswith("https://")
    # Library-state fields default to false / "" when no tracks.json.
    assert out["tracks"][0]["in_library"] is False
    assert out["tracks"][0]["apple_id"] == ""


def test_rejects_non_numeric_id(capsys: pytest.CaptureFixture) -> None:
    """`playlist-tracks abc` must not reach the resolver."""
    with patch("music_manager.cli.playlist_tracks.fetch_playlist_preview") as mock_fetch:
        exit_code = playlist_tracks.main(["abc"])
    mock_fetch.assert_not_called()
    assert exit_code != 0


def test_unknown_playlist_returns_empty_shape(capsys: pytest.CaptureFixture) -> None:
    """resolver returns the empty-shape on 404 → CLI exit 0 with same shape."""
    empty = {
        "name": "",
        "creator": "",
        "nb_tracks": 0,
        "cover_url": "",
        "tracks": [],
        "skipped_no_isrc": 0,
    }
    with patch(
        "music_manager.cli.playlist_tracks.fetch_playlist_preview",
        return_value=empty,
    ):
        exit_code = playlist_tracks.main(["999"])
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == empty


def test_resolver_exception_surfaces_as_error(capsys: pytest.CaptureFixture) -> None:
    with patch(
        "music_manager.cli.playlist_tracks.fetch_playlist_preview",
        side_effect=RuntimeError("circuit open"),
    ):
        exit_code = playlist_tracks.main(["42"])
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert "error" in payload
    assert "circuit open" in payload["error"]


def test_max_argument_is_forwarded(capsys: pytest.CaptureFixture) -> None:
    with patch(
        "music_manager.cli.playlist_tracks.fetch_playlist_preview",
        return_value={
            "name": "",
            "creator": "",
            "nb_tracks": 0,
            "tracks": [],
            "skipped_no_isrc": 0,
        },
    ) as mock_fetch:
        playlist_tracks.main(["42", "--max", "120"])
    mock_fetch.assert_called_once_with(42, max_tracks=120)


def test_default_max_is_500(capsys: pytest.CaptureFixture) -> None:
    with patch(
        "music_manager.cli.playlist_tracks.fetch_playlist_preview",
        return_value={
            "name": "",
            "creator": "",
            "nb_tracks": 0,
            "tracks": [],
            "skipped_no_isrc": 0,
        },
    ) as mock_fetch:
        playlist_tracks.main(["42"])
    mock_fetch.assert_called_once_with(42, max_tracks=500)


# ── in_library tagging (mirrors search.py semantics) ──────────────────────


def test_marks_track_as_in_library(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A track whose ISRC is in tracks.json is flagged in_library + apple_id."""
    data_root = tmp_path / "music"
    data_root.mkdir()
    (data_root / ".data").mkdir()
    (data_root / ".data" / "tracks.json").write_text(
        '{"AP_BAD_GUY": {"isrc": "FRABC1234567", "title": "Bad Guy", "apple_id": "AP_BAD_GUY"}}'
    )
    monkeypatch.setattr(
        "music_manager.cli.playlist_tracks.load_config",
        lambda: {"data_root": str(data_root)},
    )
    with (
        patch(
            "music_manager.cli.playlist_tracks.fetch_playlist_preview",
            return_value=_payload(),
        ),
        patch(
            "music_manager.cli.playlist_tracks.apple_ids_exist",
            return_value={"AP_BAD_GUY"},
        ),
    ):
        playlist_tracks.main(["42"])
    out = json.loads(capsys.readouterr().out)
    assert out["tracks"][0]["in_library"] is True
    assert out["tracks"][0]["apple_id"] == "AP_BAD_GUY"
    # Second track ISRC is not in the library → in_library False.
    assert out["tracks"][1]["in_library"] is False
    assert out["tracks"][1]["apple_id"] == ""


def test_apple_id_falls_back_to_json_key(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Older entries without an explicit `apple_id` field still resolve via JSON key."""
    data_root = tmp_path / "music"
    data_root.mkdir()
    (data_root / ".data").mkdir()
    # No apple_id field, only the JSON key.
    (data_root / ".data" / "tracks.json").write_text(
        '{"AABBCCDDEEFF0011": {"isrc": "FRABC1234567", "title": "Bad Guy"}}'
    )
    monkeypatch.setattr(
        "music_manager.cli.playlist_tracks.load_config",
        lambda: {"data_root": str(data_root)},
    )
    with (
        patch(
            "music_manager.cli.playlist_tracks.fetch_playlist_preview",
            return_value=_payload(),
        ),
        patch(
            "music_manager.cli.playlist_tracks.apple_ids_exist",
            return_value={"AABBCCDDEEFF0011"},
        ),
    ):
        playlist_tracks.main(["42"])
    out = json.loads(capsys.readouterr().out)
    assert out["tracks"][0]["in_library"] is True
    assert out["tracks"][0]["apple_id"] == "AABBCCDDEEFF0011"


def test_drops_orphaned_apple_id(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """tracks.json may reference an apple_id deleted from Apple Music — drop it."""
    data_root = tmp_path / "music"
    data_root.mkdir()
    (data_root / ".data").mkdir()
    (data_root / ".data" / "tracks.json").write_text(
        '{"AP_DELETED": {"isrc": "FRABC1234567", "apple_id": "AP_DELETED"}}'
    )
    monkeypatch.setattr(
        "music_manager.cli.playlist_tracks.load_config",
        lambda: {"data_root": str(data_root)},
    )
    with (
        patch(
            "music_manager.cli.playlist_tracks.fetch_playlist_preview",
            return_value=_payload(),
        ),
        patch(
            "music_manager.cli.playlist_tracks.apple_ids_exist",
            return_value=set(),  # AppleScript: no candidate alive
        ) as mock_check,
    ):
        playlist_tracks.main(["42"])
    mock_check.assert_called_once_with(["AP_DELETED"])
    out = json.loads(capsys.readouterr().out)
    assert out["tracks"][0]["in_library"] is False
    assert out["tracks"][0]["apple_id"] == ""


def test_works_without_tracks_json(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Missing tracks.json → all in_library False, no crash."""
    data_root = tmp_path / "music"
    data_root.mkdir()
    (data_root / ".data").mkdir()
    monkeypatch.setattr(
        "music_manager.cli.playlist_tracks.load_config",
        lambda: {"data_root": str(data_root)},
    )
    with patch(
        "music_manager.cli.playlist_tracks.fetch_playlist_preview",
        return_value=_payload(),
    ):
        playlist_tracks.main(["42"])
    out = json.loads(capsys.readouterr().out)
    for track in out["tracks"]:
        assert track["in_library"] is False
        assert track["apple_id"] == ""
