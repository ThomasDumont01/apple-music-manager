"""Tests for core/io.py."""

import json
import threading
from pathlib import Path

from music_manager.core.io import (
    convert_exportify,
    load_csv,
    load_json,
    read_csv_flexible,
    save_csv,
    save_json,
)


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


# ── Encoding ───────────────────────────────────────────────────────────────


def test_read_csv_flexible_handles_utf8_bom(tmp_path: Path) -> None:
    """Exportify writes UTF-8 with a BOM.

    Regression: read as plain utf-8, the BOM stayed glued to the first header
    ("﻿Track Name"), no title column was detected, every row was dropped
    and the drop-zone answered "empty_csv" on a perfectly valid export.
    """
    path = tmp_path / "bom.csv"
    path.write_text(
        "Track Name,Artist Name(s),Album Name,ISRC\nBad Guy,Billie Eilish,WAFL,USUM71900764\n",
        encoding="utf-8-sig",
    )

    rows = read_csv_flexible(str(path))

    assert rows == [
        {"title": "Bad Guy", "artist": "Billie Eilish", "album": "WAFL", "isrc": "USUM71900764"}
    ]


def test_convert_exportify_handles_utf8_bom(tmp_path: Path) -> None:
    """The same BOM must not defeat the in-place conversion either."""
    path = tmp_path / "playlist.csv"
    path.write_text(
        "Track Name,Artist Name(s),Album Name,ISRC\nBad Guy,Billie Eilish,WAFL,USUM71900764\n",
        encoding="utf-8-sig",
    )

    assert convert_exportify(str(path)) is True
    assert load_csv(str(path))[0]["title"] == "Bad Guy"


def test_load_csv_survives_undecodable_file(tmp_path: Path) -> None:
    """A latin-1 CSV must return nothing, not crash the whole launch."""
    path = tmp_path / "latin.csv"
    path.write_bytes(b"title,artist\nCaf\xe9,Chanteur\n")

    assert load_csv(str(path)) == []


def test_convert_exportify_survives_undecodable_file(tmp_path: Path) -> None:
    """Startup conversion of a broken CSV returns False instead of raising."""
    path = tmp_path / "latin.csv"
    path.write_bytes(b"Track Name,Artist Name(s)\nCaf\xe9,Chanteur\n")

    assert convert_exportify(str(path)) is False


# ── Non-destructive conversion ─────────────────────────────────────────────


def test_convert_exportify_keeps_the_original(tmp_path: Path) -> None:
    """Conversion drops every extra Exportify column → keep a pristine copy.

    Regression: the user's own file was overwritten in place, losing added
    date, duration, popularity and everything else Exportify exports.
    """
    path = tmp_path / "playlist.csv"
    original = (
        "Track Name,Artist Name(s),Album Name,ISRC,Added At,Popularity\n"
        "Bad Guy,Billie Eilish,WAFL,USUM71900764,2024-01-02,87\n"
    )
    path.write_text(original, encoding="utf-8")

    assert convert_exportify(str(path)) is True

    backup = tmp_path / ".originals" / "playlist.csv"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == original
    # The working copy is the reduced, standard-format one.
    assert path.read_text(encoding="utf-8").splitlines()[0] == "title,artist,album,isrc"


def test_convert_exportify_backup_is_not_overwritten(tmp_path: Path) -> None:
    """A second conversion must not clobber the pristine first backup."""
    path = tmp_path / "playlist.csv"
    path.write_text(
        "Track Name,Artist Name(s),ISRC\nBad Guy,Billie Eilish,USUM71900764\n",
        encoding="utf-8",
    )
    convert_exportify(str(path))
    backup = tmp_path / ".originals" / "playlist.csv"
    first = backup.read_text(encoding="utf-8")

    # Standard-format file now: conversion is a no-op, backup stays as it was.
    convert_exportify(str(path))

    assert backup.read_text(encoding="utf-8") == first


# ── save_json — thread safety ────────────────────────────────────────────────


def test_save_json_concurrent_writers_never_raise(tmp_path: Path) -> None:
    """Concurrent save_json() on one path must not crash.

    Regression: the atomic write used a fixed ``<path>.tmp`` for every
    writer. Two threads wrote that same temp file, the first os.replace()
    consumed it, and the second died with FileNotFoundError. In the
    recommendation feed (6 sections built in parallel) this silently killed
    whole sections — the widget just showed an empty shelf.
    """
    path = str(tmp_path / "data.json")
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def writer(index: int) -> None:
        barrier.wait()
        for _ in range(15):
            try:
                save_json(path, {"writer": index})
            except BaseException as exc:  # noqa: BLE001 - test records everything
                errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == [], f"concurrent save_json raised: {errors[:3]}"
    # The surviving file must still be readable JSON, never a truncated blob.
    assert isinstance(load_json(path).get("writer"), int)
