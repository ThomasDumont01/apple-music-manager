"""`python -m music_manager exportify-process-csv <ABS_PATH>` — drop-zone handler.

Called by the Übersicht widget when the user drops a CSV (from Exportify or
already in standard format) onto the music tab. Reads the file at the given
absolute path **without modifying it**, enriches every ISRC track via Deezer
(parallel, ~8 workers) to get cover + preview URLs, and returns the same
shape as the Deezer ``playlist-tracks`` endpoint so the widget can reuse its
``PlaylistPreview`` component.

Tracks without an ISRC are counted in ``skipped_no_isrc``. ISRCs Deezer
doesn't recognize go into ``skipped_not_on_deezer`` — the import pipeline
would fail on them anyway.

Output schema (stable, widget-consumed)::

    {"name": "<basename without .csv>", "creator": "", "nb_tracks": N,
     "cover_url": "",
     "tracks": [{"isrc": "...", "title": "...", "artist": "...",
                 "cover_url": "...", "preview_url": "...",
                 "in_library": false, "apple_id": ""}, ...],
     "skipped_no_isrc": N,
     "skipped_not_on_deezer": N,
     "source_path": "<absolute path of the dropped file>"}

Errors are surfaced as ``{"error": "..."}`` on stdout, exit code 1.
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from music_manager.core.config import Paths, load_config
from music_manager.core.io import load_json, read_csv_flexible
from music_manager.services.apple import apple_ids_exist
from music_manager.services.resolver import deezer_get

# ── Constants ────────────────────────────────────────────────────────────────

_MAX_WORKERS = 8


# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager exportify-process-csv")
    parser.add_argument("path", help="absolute path to the dropped CSV")
    parsed = parser.parse_args(args)

    path = parsed.path
    if not os.path.isabs(path) or not path.lower().endswith(".csv"):
        sys.stdout.write(json.dumps({"error": "invalid_path"}))
        return 1
    if not os.path.isfile(path):
        sys.stdout.write(json.dumps({"error": "not_found"}))
        return 1

    rows = read_csv_flexible(path)
    if not rows:
        sys.stdout.write(json.dumps({"error": "empty_csv"}))
        return 1

    pending: list[dict] = []
    seen: set[str] = set()
    skipped_no_isrc = 0
    for row in rows:
        isrc = (row.get("isrc") or "").strip().upper()
        if not isrc:
            skipped_no_isrc += 1
            continue
        if isrc in seen:
            continue
        seen.add(isrc)
        pending.append(
            {
                "isrc": isrc,
                "title": row.get("title", ""),
                "artist": row.get("artist", ""),
            }
        )

    enriched, not_on_deezer = _enrich_via_deezer(pending)

    library_index = _load_library_index()
    candidate_ids = [
        library_index[track["isrc"]]
        for track in enriched
        if library_index.get(track["isrc"])
    ]
    alive = apple_ids_exist(candidate_ids) if candidate_ids else set()
    for track in enriched:
        candidate = library_index.get(track["isrc"], "")
        apple_id = candidate if candidate in alive else ""
        track["in_library"] = bool(apple_id)
        track["apple_id"] = apple_id

    basename = os.path.splitext(os.path.basename(path))[0]
    # Fallback playlist cover : 1ère track avec un cover. Permet à
    # ``import-isrcs --playlist-cover-url`` de poser une cover Apple Music
    # cohérente même si le CSV ne fournit pas la sienne.
    fallback_cover = next(
        (t["cover_url"] for t in enriched if t.get("cover_url")),
        "",
    )
    payload = {
        "name": basename,
        "creator": "",
        "nb_tracks": len(rows),
        "cover_url": fallback_cover,
        "tracks": enriched,
        "skipped_no_isrc": skipped_no_isrc,
        "skipped_not_on_deezer": not_on_deezer,
        "source_path": path,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


# ── Private Functions ────────────────────────────────────────────────────────


def _enrich_via_deezer(pending: list[dict]) -> tuple[list[dict], int]:
    """Resolve every ISRC via Deezer in parallel. Returns (enriched, not_found_count)."""
    if not pending:
        return [], 0

    def lookup(track: dict) -> dict | None:
        data = deezer_get(f"/track/isrc:{track['isrc']}")
        if not data or "error" in data:
            return None
        album = data.get("album") or {}
        cover = (
            album.get("cover_medium")
            or album.get("cover")
            or album.get("cover_big")
            or ""
        )
        artist_obj = data.get("artist") or {}
        # Prefer the CSV's title/artist (matches what the user sees in
        # Exportify), but fall back to Deezer's value when the CSV column was
        # empty.
        return {
            "isrc": track["isrc"],
            "title": track["title"] or str(data.get("title") or ""),
            "artist": track["artist"] or str(artist_obj.get("name") or ""),
            "cover_url": str(cover),
            "preview_url": str(data.get("preview") or ""),
        }

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        results = list(pool.map(lookup, pending))

    enriched = [t for t in results if t is not None]
    not_found = sum(1 for t in results if t is None)
    return enriched, not_found


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
        stored_apple_id = str(entry.get("apple_id") or apple_id or "").strip()
        index[isrc] = stored_apple_id
    return index
