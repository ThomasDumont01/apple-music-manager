"""Tests for music_manager.cli.exportify_process_csv (drop-zone handler)."""

import json

import pytest

from music_manager.cli import exportify_process_csv


def _stub_deezer(found: dict[str, dict] | None = None):
    """Return a deezer_get stub that maps /track/isrc:XXX → ISRC found dict."""
    found = found or {}

    def fake(endpoint: str) -> dict | None:
        prefix = "/track/isrc:"
        if not endpoint.startswith(prefix):
            return None
        isrc = endpoint[len(prefix) :]
        return found.get(isrc)

    return fake


def _dz_track(
    title: str = "Bad Guy",
    artist: str = "Billie",
    cover: str = "https://e/c.jpg",
    preview: str = "https://e/p.mp3",
) -> dict:
    return {
        "title": title,
        "artist": {"name": artist},
        "album": {"cover_medium": cover},
        "preview": preview,
    }


def test_processes_standard_csv(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv = tmp_path / "Workout.csv"
    csv.write_text(
        "title,artist,album,isrc\nBad Guy,Billie,When We All,USX111\nOther,Else,Album,USX222\n"
    )
    monkeypatch.setattr(exportify_process_csv, "load_config", lambda: {"data_root": ""})
    monkeypatch.setattr(
        exportify_process_csv,
        "deezer_get",
        _stub_deezer(
            {
                "USX111": _dz_track(cover="https://c1.jpg", preview="https://p1.mp3"),
                "USX222": _dz_track(cover="https://c2.jpg", preview="https://p2.mp3"),
            }
        ),
    )
    monkeypatch.setattr(exportify_process_csv, "apple_ids_exist", lambda _ids: set())
    exit_code = exportify_process_csv.main([str(csv)])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "Workout"
    assert len(out["tracks"]) == 2
    assert out["tracks"][0]["isrc"] == "USX111"
    assert out["tracks"][0]["cover_url"] == "https://c1.jpg"
    assert out["tracks"][0]["preview_url"] == "https://p1.mp3"
    assert out["tracks"][0]["in_library"] is False
    assert out["tracks"][0]["apple_id"] == ""
    assert out["skipped_no_isrc"] == 0
    assert out["skipped_not_on_deezer"] == 0


def test_processes_exportify_csv_without_modifying(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv = tmp_path / "Liked.csv"
    original = (
        "Track Name,Artist Name(s),Album Name,ISRC\nBad Guy,Billie Eilish,When We All,USX111\n"
    )
    csv.write_text(original)
    monkeypatch.setattr(exportify_process_csv, "load_config", lambda: {"data_root": ""})
    monkeypatch.setattr(
        exportify_process_csv,
        "deezer_get",
        _stub_deezer({"USX111": _dz_track()}),
    )
    monkeypatch.setattr(exportify_process_csv, "apple_ids_exist", lambda _ids: set())
    exportify_process_csv.main([str(csv)])
    # The source file MUST NOT be modified.
    assert csv.read_text() == original
    out = json.loads(capsys.readouterr().out)
    assert len(out["tracks"]) == 1
    assert out["tracks"][0]["title"] == "Bad Guy"


def test_skips_tracks_without_isrc(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv = tmp_path / "Mixed.csv"
    csv.write_text("title,artist,album,isrc\nA,B,C,USX111\nD,E,F,\nG,H,I,USX222\n")
    monkeypatch.setattr(exportify_process_csv, "load_config", lambda: {"data_root": ""})
    monkeypatch.setattr(
        exportify_process_csv,
        "deezer_get",
        _stub_deezer({"USX111": _dz_track(), "USX222": _dz_track()}),
    )
    monkeypatch.setattr(exportify_process_csv, "apple_ids_exist", lambda _ids: set())
    exportify_process_csv.main([str(csv)])
    out = json.loads(capsys.readouterr().out)
    assert len(out["tracks"]) == 2
    assert out["skipped_no_isrc"] == 1
    assert out["skipped_not_on_deezer"] == 0


def test_counts_not_on_deezer(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv = tmp_path / "Mix.csv"
    csv.write_text("title,artist,album,isrc\nA,B,C,USX111\nD,E,F,USX_UNKNOWN\n")
    monkeypatch.setattr(exportify_process_csv, "load_config", lambda: {"data_root": ""})
    # Only USX111 is in Deezer; USX_UNKNOWN returns None.
    monkeypatch.setattr(
        exportify_process_csv,
        "deezer_get",
        _stub_deezer({"USX111": _dz_track()}),
    )
    monkeypatch.setattr(exportify_process_csv, "apple_ids_exist", lambda _ids: set())
    exportify_process_csv.main([str(csv)])
    out = json.loads(capsys.readouterr().out)
    assert len(out["tracks"]) == 1
    assert out["tracks"][0]["isrc"] == "USX111"
    assert out["skipped_not_on_deezer"] == 1


def test_deduplicates_isrcs(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv = tmp_path / "Dups.csv"
    csv.write_text("title,artist,album,isrc\nA,B,C,usx111\nA,B,C,USX111\nD,E,F,USX222\n")
    monkeypatch.setattr(exportify_process_csv, "load_config", lambda: {"data_root": ""})
    monkeypatch.setattr(
        exportify_process_csv,
        "deezer_get",
        _stub_deezer({"USX111": _dz_track(), "USX222": _dz_track()}),
    )
    monkeypatch.setattr(exportify_process_csv, "apple_ids_exist", lambda _ids: set())
    exportify_process_csv.main([str(csv)])
    out = json.loads(capsys.readouterr().out)
    isrcs = [t["isrc"] for t in out["tracks"]]
    assert isrcs == ["USX111", "USX222"]


def test_enriches_in_library(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "music"
    (data_root / ".data").mkdir(parents=True)
    (data_root / ".data" / "tracks.json").write_text(
        '{"AP_BG": {"isrc": "USX111", "apple_id": "AP_BG"}}'
    )
    csv = tmp_path / "L.csv"
    csv.write_text("title,artist,album,isrc\nA,B,C,USX111\n")
    monkeypatch.setattr(
        exportify_process_csv,
        "load_config",
        lambda: {"data_root": str(data_root)},
    )
    monkeypatch.setattr(
        exportify_process_csv,
        "deezer_get",
        _stub_deezer({"USX111": _dz_track()}),
    )
    monkeypatch.setattr(exportify_process_csv, "apple_ids_exist", lambda _ids: {"AP_BG"})
    exportify_process_csv.main([str(csv)])
    out = json.loads(capsys.readouterr().out)
    assert out["tracks"][0]["in_library"] is True
    assert out["tracks"][0]["apple_id"] == "AP_BG"


def test_rejects_relative_path(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exportify_process_csv, "load_config", lambda: {"data_root": ""})
    exit_code = exportify_process_csv.main(["./relative.csv"])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "invalid_path"


def test_rejects_non_csv(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "data.json"
    other.write_text("{}")
    monkeypatch.setattr(exportify_process_csv, "load_config", lambda: {"data_root": ""})
    exit_code = exportify_process_csv.main([str(other)])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "invalid_path"


def test_not_found(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exportify_process_csv, "load_config", lambda: {"data_root": ""})
    exit_code = exportify_process_csv.main([str(tmp_path / "nope.csv")])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "not_found"


def test_empty_csv(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv = tmp_path / "Empty.csv"
    csv.write_text("title,artist,album,isrc\n")
    monkeypatch.setattr(exportify_process_csv, "load_config", lambda: {"data_root": ""})
    exit_code = exportify_process_csv.main([str(csv)])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "empty_csv"


def test_source_path_included(
    tmp_path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv = tmp_path / "X.csv"
    csv.write_text("title,artist,album,isrc\nA,B,C,USX111\n")
    monkeypatch.setattr(exportify_process_csv, "load_config", lambda: {"data_root": ""})
    monkeypatch.setattr(
        exportify_process_csv,
        "deezer_get",
        _stub_deezer({"USX111": _dz_track()}),
    )
    monkeypatch.setattr(exportify_process_csv, "apple_ids_exist", lambda _ids: set())
    exportify_process_csv.main([str(csv)])
    out = json.loads(capsys.readouterr().out)
    assert out["source_path"] == str(csv)
