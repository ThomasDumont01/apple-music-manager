"""Tests for core/io.py."""

import json
from pathlib import Path

from music_manager.core.io import convert_exportify, load_csv, load_json, save_csv, save_json


def test_load_json_returns_dict(tmp_path: Path) -> None:
    """Load a valid JSON file."""
    path = str(tmp_path / "test.json")
    with open(path, "w") as file:
        json.dump({"key": "value"}, file)

    assert load_json(path) == {"key": "value"}


def test_load_json_missing_file() -> None:
    """Missing file returns empty dict."""
    assert load_json("/nonexistent/path.json") == {}


def test_load_json_corrupt_file(tmp_path: Path) -> None:
    """Corrupt JSON returns empty dict."""
    path = str(tmp_path / "bad.json")
    with open(path, "w") as file:
        file.write("{not valid json")

    assert load_json(path) == {}


def test_save_json_atomic(tmp_path: Path) -> None:
    """Save creates the file with correct content."""
    path = str(tmp_path / "out.json")
    save_json(path, {"hello": "world"})

    with open(path) as file:
        assert json.load(file) == {"hello": "world"}


def test_save_json_creates_dirs(tmp_path: Path) -> None:
    """Save creates parent directories if missing."""
    path = str(tmp_path / "deep" / "nested" / "file.json")
    save_json(path, {"nested": True})

    assert load_json(path) == {"nested": True}


def test_convert_exportify_detects_and_converts(tmp_path: Path) -> None:
    """Exportify CSV with French columns is converted to standard format."""
    csv_path = str(tmp_path / "playlist.csv")
    with open(csv_path, "w") as file:
        file.write('"Nom du titre","Nom(s) de l\'artiste","Nom de l\'album","ISRC"\n')
        file.write('"Bohemian Rhapsody","Queen","A Night at the Opera","GBUM71029604"\n')

    assert convert_exportify(csv_path) is True

    rows = load_csv(csv_path)
    assert len(rows) == 1
    assert rows[0]["title"] == "Bohemian Rhapsody"
    assert rows[0]["artist"] == "Queen"
    assert rows[0]["isrc"] == "GBUM71029604"


def test_convert_exportify_ignores_standard_csv(tmp_path: Path) -> None:
    """Standard CSV (already correct columns) is not converted."""
    csv_path = str(tmp_path / "standard.csv")
    with open(csv_path, "w") as file:
        file.write("title,artist,album\n")
        file.write("Imagine,John Lennon,Imagine\n")

    assert convert_exportify(csv_path) is False


# ── save_csv ─────────────────────────────────────────────────────────────


def test_save_csv_roundtrip(tmp_path):
    """save_csv + load_csv roundtrip preserves data."""

    rows = [
        {"title": "Song 1", "artist": "Art 1", "album": "Al 1", "isrc": "ISRC1"},
        {"title": "Song 2", "artist": "Art 2", "album": "Al 2"},
    ]
    fp = str(tmp_path / "test.csv")
    save_csv(fp, rows)
    loaded = load_csv(fp)
    assert len(loaded) == 2
    assert loaded[0]["title"] == "Song 1"
    assert loaded[0]["isrc"] == "ISRC1"
    assert loaded[1]["artist"] == "Art 2"


def test_save_csv_creates_dirs(tmp_path):
    """save_csv creates parent directories."""

    fp = str(tmp_path / "sub" / "dir" / "test.csv")
    save_csv(fp, [{"title": "S", "artist": "A", "album": "B"}])
    assert (tmp_path / "sub" / "dir" / "test.csv").exists()


def test_save_csv_empty(tmp_path):
    """save_csv with empty rows creates file with headers only."""

    fp = str(tmp_path / "empty.csv")
    save_csv(fp, [])
    loaded = load_csv(fp)
    assert len(loaded) == 0
