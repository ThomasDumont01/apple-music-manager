"""Tests for music_manager/cli/search.py."""

import json
from unittest.mock import patch

import pytest

from music_manager.cli import search

# ── Helpers ─────────────────────────────────────────────────────────────────


def _deezer_item(
    *,
    isrc: str = "frabc1234567",
    title: str = "Bad Guy",
    artist: str = "Billie Eilish",
    album: str = "When We All Fall Asleep",
    track_id: int = 645942392,
    duration: int = 194,
    preview: str = "https://e-cdns-preview.deezer.com/bad.mp3",
    cover: str = "https://cdns.deezer.com/cover.jpg",
    explicit: bool = False,
) -> dict:
    return {
        "id": track_id,
        "title": title,
        "isrc": isrc,
        "duration": duration,
        "explicit_lyrics": explicit,
        "preview": preview,
        "artist": {"name": artist},
        "album": {"title": album, "cover_medium": cover},
    }


# ── search command ─────────────────────────────────────────────────────────


def test_search_outputs_stable_json_schema(capsys: pytest.CaptureFixture) -> None:
    """The command serializes search hits onto the schema the widget consumes."""
    with patch(
        "music_manager.cli.search.search_deezer_free",
        return_value=[_deezer_item()],
    ):
        exit_code = search.main(["billie eilish bad guy"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    item = payload[0]
    assert item["isrc"] == "FRABC1234567"  # uppercased
    assert item["title"] == "Bad Guy"
    assert item["artist"] == "Billie Eilish"
    assert item["album"] == "When We All Fall Asleep"
    assert item["deezer_id"] == 645942392
    assert item["duration"] == 194
    assert item["preview_url"].startswith("https://")
    assert item["cover_url"].startswith("https://")
    assert item["explicit"] is False


def test_search_passes_limit_argument(capsys: pytest.CaptureFixture) -> None:
    """--limit is forwarded to the resolver."""
    with patch(
        "music_manager.cli.search.search_deezer_free", return_value=[]
    ) as mock_search:
        search.main(["query", "--limit", "5"])
    mock_search.assert_called_once_with("query", 5)


def test_search_returns_empty_array_for_empty_results(
    capsys: pytest.CaptureFixture,
) -> None:
    """No matches → '[]' on stdout, exit 0 (not an error)."""
    with patch("music_manager.cli.search.search_deezer_free", return_value=[]):
        exit_code = search.main(["nothing matches"])
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == []


def test_search_handles_resolver_exception(capsys: pytest.CaptureFixture) -> None:
    """Any resolver exception is captured into {'error': ...}, exit 1."""
    with patch(
        "music_manager.cli.search.search_deezer_free",
        side_effect=RuntimeError("Deezer offline"),
    ):
        exit_code = search.main(["query"])
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert "error" in payload
    assert "Deezer offline" in payload["error"]


def test_search_default_limit_is_ten(capsys: pytest.CaptureFixture) -> None:
    """Without --limit, the CLI defaults to 10 results."""
    with patch(
        "music_manager.cli.search.search_deezer_free", return_value=[]
    ) as mock_search:
        search.main(["query"])
    mock_search.assert_called_once_with("query", 10)


def test_search_skips_non_dict_items(capsys: pytest.CaptureFixture) -> None:
    """Defensive: malformed Deezer payloads don't crash the CLI."""
    with patch(
        "music_manager.cli.search.search_deezer_free",
        return_value=[_deezer_item(), "garbage", None, _deezer_item(title="Other")],
    ):
        search.main(["q"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 2
    assert payload[1]["title"] == "Other"


# ── in_library flag ────────────────────────────────────────────────────────


def test_search_marks_track_as_in_library(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """A track whose ISRC is in tracks.json is flagged in_library=True + apple_id."""
    data_root = tmp_path / "music"
    data_root.mkdir()
    (data_root / ".data").mkdir()
    (data_root / ".data" / "tracks.json").write_text(
        '{"9878CAFBC2B2BB75": {"isrc": "FRABC1234567", "title": "Bad Guy", '
        '"apple_id": "9878CAFBC2B2BB75"}}'
    )
    monkeypatch.setattr(
        "music_manager.cli.search.load_config",
        lambda: {"data_root": str(data_root)},
    )
    with (
        patch(
            "music_manager.cli.search.search_deezer_free",
            return_value=[_deezer_item(isrc="FRABC1234567")],
        ),
        patch(
            "music_manager.cli.search.apple_ids_exist",
            return_value={"9878CAFBC2B2BB75"},
        ),
    ):
        search.main(["q"])
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["in_library"] is True
    assert payload[0]["apple_id"] == "9878CAFBC2B2BB75"


def test_search_apple_id_falls_back_to_json_key(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture
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
        "music_manager.cli.search.load_config",
        lambda: {"data_root": str(data_root)},
    )
    with (
        patch(
            "music_manager.cli.search.search_deezer_free",
            return_value=[_deezer_item(isrc="FRABC1234567")],
        ),
        patch(
            "music_manager.cli.search.apple_ids_exist",
            return_value={"AABBCCDDEEFF0011"},
        ),
    ):
        search.main(["q"])
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["apple_id"] == "AABBCCDDEEFF0011"
    assert payload[0]["in_library"] is True


def test_search_marks_track_not_in_library(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """An ISRC absent from tracks.json yields in_library=False + empty apple_id."""
    data_root = tmp_path / "music"
    data_root.mkdir()
    (data_root / ".data").mkdir()
    (data_root / ".data" / "tracks.json").write_text(
        '{"AP1": {"isrc": "OTHERXXX0000"}}'
    )
    monkeypatch.setattr(
        "music_manager.cli.search.load_config",
        lambda: {"data_root": str(data_root)},
    )
    with patch(
        "music_manager.cli.search.search_deezer_free",
        return_value=[_deezer_item(isrc="FRABC1234567")],
    ):
        search.main(["q"])
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["in_library"] is False
    assert payload[0]["apple_id"] == ""


def test_search_works_without_tracks_json(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """Missing tracks.json → in_library=False, no crash."""
    data_root = tmp_path / "music"
    data_root.mkdir()
    (data_root / ".data").mkdir()
    monkeypatch.setattr(
        "music_manager.cli.search.load_config",
        lambda: {"data_root": str(data_root)},
    )
    with patch(
        "music_manager.cli.search.search_deezer_free",
        return_value=[_deezer_item()],
    ):
        search.main(["q"])
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["in_library"] is False


def test_search_works_without_data_root(
    monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """Unconfigured data root → in_library=False, no crash."""
    monkeypatch.setattr(
        "music_manager.cli.search.load_config",
        lambda: {"data_root": ""},
    )
    with patch(
        "music_manager.cli.search.search_deezer_free",
        return_value=[_deezer_item()],
    ):
        search.main(["q"])
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["in_library"] is False


def test_search_ignores_case_when_matching_isrc(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """tracks.json may store lowercase ISRC — the match is uppercase-canonical."""
    data_root = tmp_path / "music"
    data_root.mkdir()
    (data_root / ".data").mkdir()
    (data_root / ".data" / "tracks.json").write_text(
        '{"AP1": {"isrc": "frabc1234567"}}'
    )
    monkeypatch.setattr(
        "music_manager.cli.search.load_config",
        lambda: {"data_root": str(data_root)},
    )
    with (
        patch(
            "music_manager.cli.search.search_deezer_free",
            return_value=[_deezer_item(isrc="FRABC1234567")],
        ),
        patch(
            "music_manager.cli.search.apple_ids_exist",
            return_value={"AP1"},
        ),
    ):
        search.main(["q"])
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["in_library"] is True


def test_search_drops_orphaned_apple_id(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """tracks.json may reference an apple_id that the user since deleted from
    Apple Music — the search must not flag it as in_library."""
    data_root = tmp_path / "music"
    data_root.mkdir()
    (data_root / ".data").mkdir()
    (data_root / ".data" / "tracks.json").write_text(
        '{"AP_DELETED": {"isrc": "FRABC1234567", "apple_id": "AP_DELETED"}}'
    )
    monkeypatch.setattr(
        "music_manager.cli.search.load_config",
        lambda: {"data_root": str(data_root)},
    )
    with (
        patch(
            "music_manager.cli.search.search_deezer_free",
            return_value=[_deezer_item(isrc="FRABC1234567")],
        ),
        # AppleScript replies "this ID doesn't exist anymore" → empty set.
        patch(
            "music_manager.cli.search.apple_ids_exist",
            return_value=set(),
        ) as mock_check,
    ):
        search.main(["q"])
    mock_check.assert_called_once_with(["AP_DELETED"])
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["in_library"] is False
    assert payload[0]["apple_id"] == ""


def test_search_skips_apple_check_when_no_candidates(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """No tracks.json hit → no AppleScript spawn (perf path)."""
    data_root = tmp_path / "music"
    data_root.mkdir()
    (data_root / ".data").mkdir()
    # tracks.json exists but ISRC doesn't match the search result.
    (data_root / ".data" / "tracks.json").write_text(
        '{"AP1": {"isrc": "OTHER0000001"}}'
    )
    monkeypatch.setattr(
        "music_manager.cli.search.load_config",
        lambda: {"data_root": str(data_root)},
    )
    with (
        patch(
            "music_manager.cli.search.search_deezer_free",
            return_value=[_deezer_item(isrc="FRABC1234567")],
        ),
        patch(
            "music_manager.cli.search.apple_ids_exist"
        ) as mock_check,
    ):
        search.main(["q"])
    mock_check.assert_not_called()
