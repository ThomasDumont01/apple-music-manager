"""`python -m music_manager playlist-tracks DEEZER_PLAYLIST_ID [--max N]`.

Used by the Übersicht widget when the user clicks a Deezer playlist result.
Returns preview-ready metadata for every importable track behind the playlist
so the widget can show a preview list before kicking off the actual import
(via ``import-isrcs --playlist-name "<name>"``).

Output schema (stable)::

    {"name": "Chill", "creator": "thomas", "nb_tracks": 42,
     "tracks": [{"isrc": "FRABC1234567", "title": "Bad Guy",
                 "artist": "Billie Eilish",
                 "cover_url": "https://...",
                 "preview_url": "https://...",
                 "in_library": false, "apple_id": ""}, ...],
     "skipped_no_isrc": 2}

``in_library`` is true when the track's ISRC is already present in the
user's tracks.json — same semantics as the ``search`` command.

Errors are surfaced as ``{"error": "..."}`` on stdout, exit code 1.
Invalid id (non-numeric) → exit code 2.
"""

import argparse
import json
import os
import sys

from music_manager.core.config import Paths, load_config
from music_manager.core.io import load_json
from music_manager.services.apple import apple_ids_exist
from music_manager.services.resolver import configure, fetch_playlist_preview

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager playlist-tracks")
    parser.add_argument("playlist_id", type=int, help="Deezer playlist id")
    parser.add_argument(
        "--max",
        type=int,
        default=500,
        dest="max_tracks",
        help="hard cap on tracks fetched (default 500)",
    )
    try:
        parsed = parser.parse_args(args)
    except SystemExit as exc:
        # argparse exits 2 on invalid int — let it bubble up so the widget can
        # distinguish "wrong invocation" from "Deezer error".
        return int(exc.code or 2)

    configure("fr")

    try:
        payload = fetch_playlist_preview(parsed.playlist_id, max_tracks=parsed.max_tracks)
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": str(exc)[:200]}))
        return 1

    library_index = _load_library_index()
    # Pré-collecte les apple_id candidats (ceux que tracks.json prétend avoir
    # importés et qui apparaissent dans la playlist) pour éviter de scanner
    # toute la lib Apple Music. On vérifie ensuite lesquels existent encore.
    tracks_list = payload.get("tracks", [])
    candidate_ids = [
        library_index[track.get("isrc", "")]
        for track in tracks_list
        if track.get("isrc") and library_index.get(track["isrc"])
    ]
    alive = apple_ids_exist(candidate_ids) if candidate_ids else set()
    for track in tracks_list:
        isrc = track.get("isrc", "")
        candidate = library_index.get(isrc, "") if isrc else ""
        apple_id = candidate if candidate in alive else ""
        track["in_library"] = bool(apple_id)
        track["apple_id"] = apple_id

    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
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
        stored_apple_id = str(entry.get("apple_id") or apple_id or "").strip()
        index[isrc] = stored_apple_id
    return index
