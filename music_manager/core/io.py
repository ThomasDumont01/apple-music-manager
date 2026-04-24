"""Atomic JSON read/write for persistent data files."""

import csv
import json
import os
from collections.abc import Mapping
from typing import Any

# ── Constants ──────────────────────────────────────────────────────────────

_CSV_BASE = ["title", "artist", "album"]
_CSV_EXTRA = ["isrc"]

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
    """Write dict to JSON atomically (tmp + replace)."""
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
        with open(path, encoding="utf-8") as file:
            for row in csv.DictReader(file):
                title = row.get("title", "").strip()
                artist = row.get("artist", "").strip()
                if not title or not artist:
                    continue
                entry: dict = {
                    "title": title,
                    "artist": artist,
                    "album": row.get("album", "").strip(),
                }
                isrc = row.get("isrc", "").strip()
                if isrc:
                    entry["isrc"] = isrc
                rows.append(entry)
    except FileNotFoundError:
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


def convert_exportify(path: str) -> bool:
    """Detect and convert an Exportify/Spotify CSV to standard format.

    Returns True if a conversion was performed.
    """
    try:
        with open(path, encoding="utf-8") as file:
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
                title = row.get(col_map["title"] or "", "").strip()
                artist = row.get(col_map["artist"] or "", "").strip()
                if not title or not artist:
                    continue
                entry = {
                    "title": title,
                    "artist": artist,
                    "album": row.get(col_map.get("album") or "", "").strip(),
                }
                isrc = row.get(col_map.get("isrc") or "", "").strip()
                if isrc:
                    entry["isrc"] = isrc
                tracks.append(entry)
    except FileNotFoundError:
        return False

    save_csv(path, tracks)
    return True
