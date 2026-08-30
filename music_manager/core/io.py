"""Atomic JSON read/write for persistent data files."""

import csv
import json
import os
import shutil
import threading
from collections.abc import Mapping
from typing import Any

# ── Constants ──────────────────────────────────────────────────────────────

# Atomic writes share a fixed "<path>.tmp" so load_json() can recover from
# it after a crash. That shared name also means two threads writing the
# same file would fight over one temp file, and the loser's os.replace()
# would die on FileNotFoundError. Serialize writers instead of renaming the
# temp file, which would break the recovery contract.
_WRITE_LOCK = threading.RLock()

_CSV_BASE = ["title", "artist", "album"]
_CSV_EXTRA = ["isrc"]

# Exportify (and Excel) write UTF-8 with a BOM. Reading those as plain "utf-8"
# leaves "﻿" glued to the first header, so "Track Name" never matches and
# every row is dropped — the whole playlist silently imports as empty.
_CSV_ENCODING = "utf-8-sig"

# Where the untouched original is kept when an Exportify CSV is converted in
# place. Hidden so it never shows up in the playlist listings.
_ORIGINALS_DIRNAME = ".originals"

_EXPORTIFY_COLS = {
    "title": ("Track Name", "Nom du titre"),
    "artist": ("Artist Name(s)", "Nom(s) de l'artiste"),
    "album": ("Album Name", "Nom de l'album"),
    "isrc": ("ISRC",),
}


# ── Entry point ──────────────────────────────────────────────────────────────


def load_json(path: str) -> dict[str, Any]:
    """Load a JSON file as dict. Returns empty dict if missing.

    On corruption (invalid JSON), attempts recovery from .tmp backup.
    Logs a warning on data loss.
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        # Attempt recovery from atomic write backup (.tmp)
        tmp_path = path + ".tmp"
        if os.path.isfile(tmp_path):
            try:
                with open(tmp_path, encoding="utf-8") as file:
                    data = json.load(file)
                    if isinstance(data, dict):
                        # Restore from backup
                        os.replace(tmp_path, path)
                        return data
            except (json.JSONDecodeError, OSError):
                pass
        # No recovery possible — log the data loss
        import sys  # noqa: PLC0415

        print(
            f"WARNING: corrupt JSON at {path}: {exc}. Data lost.",
            file=sys.stderr,
        )
        return {}


def save_json(path: str, data: Mapping[str, object]) -> None:
    """Write dict to JSON atomically (tmp + replace). Thread-safe."""
    with _WRITE_LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def load_csv(path: str) -> list[dict[str, str]]:
    """Load a CSV file. Required columns: title, artist. Album and isrc optional.

    Returns a list of dicts with keys: title, artist, album, isrc.
    Rows missing title or artist are skipped.
    """
    rows: list[dict] = []
    try:
        with open(path, encoding=_CSV_ENCODING) as file:
            for row in csv.DictReader(file):
                title = (row.get("title") or "").strip()
                artist = (row.get("artist") or "").strip()
                if not title or not artist:
                    continue
                entry: dict = {
                    "title": title,
                    "artist": artist,
                    "album": (row.get("album") or "").strip(),
                }
                isrc = (row.get("isrc") or "").strip()
                if isrc:
                    entry["isrc"] = isrc
                rows.append(entry)
    except (FileNotFoundError, UnicodeDecodeError, csv.Error):
        pass
    return rows


def save_csv(path: str, rows: list[dict[str, str]]) -> None:
    """Write rows to CSV atomically. Auto-detects extra columns from data."""
    extra = [col for col in _CSV_EXTRA if any(col in row for row in rows)]
    fieldnames = _CSV_BASE + extra

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp_path, path)


def read_csv_flexible(path: str) -> list[dict[str, str]]:
    """Read a CSV in either Exportify or standard format. Does not modify the file.

    Detects Exportify column names (``Track Name``, ``Artist Name(s)``, etc.)
    or falls back to standard names (``title``, ``artist``, ``album``,
    ``isrc``). Returns rows in the standard shape so callers don't have to
    care which format they have.
    """
    rows: list[dict] = []
    try:
        with open(path, encoding=_CSV_ENCODING) as file:
            reader = csv.DictReader(file)
            fieldnames = reader.fieldnames or []
            col_map = {
                key: next((col for col in candidates if col in fieldnames), None)
                for key, candidates in _EXPORTIFY_COLS.items()
            }
            standard = {
                "title": "title",
                "artist": "artist",
                "album": "album",
                "isrc": "isrc",
            }
            for key, default_col in standard.items():
                if not col_map.get(key):
                    col_map[key] = default_col if default_col in fieldnames else None
            for row in reader:
                title = (row.get(col_map.get("title") or "") or "").strip()
                artist = (row.get(col_map.get("artist") or "") or "").strip()
                if not title or not artist:
                    continue
                entry: dict = {
                    "title": title,
                    "artist": artist,
                    "album": (row.get(col_map.get("album") or "") or "").strip(),
                }
                isrc = (row.get(col_map.get("isrc") or "") or "").strip()
                if isrc:
                    entry["isrc"] = isrc
                rows.append(entry)
    except (OSError, UnicodeDecodeError, csv.Error):
        pass
    return rows


def convert_exportify(path: str) -> bool:
    """Detect and convert an Exportify/Spotify CSV to standard format.

    The original is copied into a hidden ``.originals/`` folder next to it
    first: the conversion keeps only title/artist/album/isrc, so it used to
    destroy every other Exportify column (added date, duration, popularity…)
    of a file the user had produced by hand.

    Returns True if a conversion was performed.
    """
    try:
        with open(path, encoding=_CSV_ENCODING) as file:
            reader = csv.DictReader(file)
            fieldnames = reader.fieldnames or []

            col_map = {
                key: next((col for col in candidates if col in fieldnames), None)
                for key, candidates in _EXPORTIFY_COLS.items()
            }
            if not col_map["title"] or not col_map["artist"]:
                return False

            tracks = []
            for row in reader:
                title = (row.get(col_map["title"] or "") or "").strip()
                artist = (row.get(col_map["artist"] or "") or "").strip()
                if not title or not artist:
                    continue
                entry = {
                    "title": title,
                    "artist": artist,
                    "album": (row.get(col_map.get("album") or "") or "").strip(),
                }
                isrc = (row.get(col_map.get("isrc") or "") or "").strip()
                if isrc:
                    entry["isrc"] = isrc
                tracks.append(entry)
    except (OSError, UnicodeDecodeError, csv.Error):
        return False

    _backup_original(path)
    save_csv(path, tracks)
    return True


# ── Private Functions ────────────────────────────────────────────────────────


def _backup_original(path: str) -> None:
    """Keep an untouched copy of ``path`` before rewriting it. Best-effort."""
    try:
        originals = os.path.join(os.path.dirname(path), _ORIGINALS_DIRNAME)
        os.makedirs(originals, exist_ok=True)
        target = os.path.join(originals, os.path.basename(path))
        if not os.path.exists(target):
            shutil.copy2(path, target)
    except OSError:
        pass
