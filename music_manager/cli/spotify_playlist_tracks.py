"""`python -m music_manager spotify-playlist-tracks <id|liked> [--max N]`.

Resolves a Spotify playlist (or the user's liked tracks if ``id == "liked"``)
into the same preview shape as the Deezer ``playlist-tracks`` command so the
Übersicht widget can reuse its ``PlaylistPreview`` component verbatim.

Output schema (stable, widget-consumed)::

    {"name": "...", "creator": "...", "nb_tracks": N, "cover_url": "...",
     "tracks": [{"isrc": "...", "title": "...", "artist": "...",
                 "cover_url": "...", "preview_url": "...",
                 "in_library": false, "apple_id": ""}, ...],
     "skipped_no_isrc": N}

Errors are surfaced as ``{"error": "..."}`` on stdout, exit code 1.
"""

import argparse
import json
import os
import sys

from music_manager.core.config import Paths, load_config
from music_manager.core.io import load_json
from music_manager.services.apple import apple_ids_exist
from music_manager.services.spotify import (
    LIKED_TRACKS_ID,
    fetch_liked_tracks,
    fetch_spotify_playlist_preview,
)

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager spotify-playlist-tracks")
    parser.add_argument(
        "playlist_id",
        help='Spotify playlist id, or the literal "liked" for liked tracks',
    )
    parser.add_argument(
        "--max",
        type=int,
        default=500,
        dest="max_tracks",
        help="hard cap on tracks fetched (default 500)",
    )
    parsed = parser.parse_args(args)

    try:
        if parsed.playlist_id.strip().lower() == LIKED_TRACKS_ID:
            payload = fetch_liked_tracks(max_tracks=parsed.max_tracks)
        else:
            payload = fetch_spotify_playlist_preview(
                parsed.playlist_id, max_tracks=parsed.max_tracks
            )
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": str(exc)[:200]}))
        return 1

    library_index = _load_library_index()
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
