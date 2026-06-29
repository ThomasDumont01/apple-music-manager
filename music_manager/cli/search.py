"""`python -m music_manager search "query" [--limit N]` — JSON stdout.

Used by the Übersicht widget to power the live search box. Output schema
is stable (the widget parses it directly):

    [
      {"isrc": "FRABC1234567", "title": "...", "artist": "...",
       "album": "...", "deezer_id": 123, "duration": 220,
       "preview_url": "https://...", "cover_url": "https://...",
       "explicit": false, "in_library": false},
      ...
    ]

``in_library`` is true when the track's ISRC is already present in the
user's tracks.json — the widget shows a passive music icon instead of the
"+" button to avoid duplicate imports.

Errors are surfaced as ``{"error": "..."}`` on stdout, exit code 1.
"""

import argparse
import json
import os
import sys

from music_manager.core.config import Paths, load_config
from music_manager.core.io import load_json
from music_manager.services.apple import apple_ids_exist
from music_manager.services.resolver import configure, search_deezer_free

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    """CLI entry. Returns the exit code."""
    parser = argparse.ArgumentParser(prog="music_manager search")
    parser.add_argument("query", help="free-text search query")
    parser.add_argument(
        "--limit", type=int, default=10, help="max results (default 10, capped at 50)"
    )
    parsed = parser.parse_args(args)

    # Resolver state (HTTP session, language) — same defaults as the UI launcher.
    configure("fr")

    try:
        raw = search_deezer_free(parsed.query, parsed.limit)
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": str(exc)[:200]}))
        return 1

    library_index = _load_library_index()
    # Filtrer les apple_id orphelins : tracks.json peut référencer des IDs
    # supprimés depuis Apple Music. On ne valide que les IDs qui apparaissent
    # dans les résultats — pas le store entier (coût AppleScript).
    candidate_ids = [
        library_index[(item.get("isrc") or "").upper()]
        for item in raw
        if isinstance(item, dict) and library_index.get((item.get("isrc") or "").upper())
    ]
    alive = apple_ids_exist(candidate_ids) if candidate_ids else set()
    library_index = (
        {isrc: aid for isrc, aid in library_index.items() if aid in alive} if candidate_ids else {}
    )
    results = [_format_track(item, library_index) for item in raw if isinstance(item, dict)]
    sys.stdout.write(json.dumps(results, ensure_ascii=False))
    return 0


# ── Private Functions ────────────────────────────────────────────────────────


def _load_library_index() -> dict[str, str]:
    """Map known ISRC (upper) → apple_id, read from tracks.json."""
    config = load_config()
    data_root = str(config.get("data_root") or "")
    if not data_root or not os.path.isdir(data_root):
        return {}
    paths = Paths(data_root)
    if not os.path.isfile(paths.tracks_path):
        return {}
    data = load_json(paths.tracks_path)
    index: dict[str, str] = {}
    for apple_id, entry in data.items():
        if not isinstance(entry, dict):
            continue
        isrc = str(entry.get("isrc") or "").strip().upper()
        if not isrc:
            continue
        # Prefer the stored apple_id field; fall back to the JSON key (which
        # is the Apple Music persistent ID for entries hydrated by the scan).
        stored_apple_id = str(entry.get("apple_id") or apple_id or "").strip()
        index[isrc] = stored_apple_id
    return index


def _format_track(item: dict, library_index: dict[str, str]) -> dict:
    """Project the Deezer track dict onto the stable widget schema."""
    artist_obj = item.get("artist") or {}
    album_obj = item.get("album") or {}
    isrc = str(item.get("isrc") or "").upper()
    apple_id = library_index.get(isrc, "") if isrc else ""
    return {
        "isrc": isrc,
        "title": str(item.get("title") or ""),
        "artist": str(artist_obj.get("name") or ""),
        "album": str(album_obj.get("title") or ""),
        "deezer_id": int(item.get("id") or 0),
        "duration": int(item.get("duration") or 0),
        "preview_url": str(item.get("preview") or ""),
        "cover_url": str(album_obj.get("cover_medium") or album_obj.get("cover") or ""),
        "explicit": bool(item.get("explicit_lyrics")),
        "in_library": bool(apple_id),
        "apple_id": apple_id,
    }
