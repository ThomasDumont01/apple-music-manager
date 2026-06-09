"""`python -m music_manager spotify-playlists` — list user playlists + liked.

Output schema (stable, widget-consumed)::

    [
      {"spotify_id": "liked", "title": "♥ Titres likés", "nb_tracks": N,
       "picture_url": "", "creator": ""},
      {"spotify_id": "...", "title": "...", "nb_tracks": N,
       "picture_url": "...", "creator": "..."},
      ...
    ]

The ``liked`` virtual entry is always first; the widget treats ``spotify_id ==
"liked"`` specially and calls ``spotify-playlist-tracks liked`` to fetch its
content.

Errors are surfaced as ``{"error": "..."}`` on stdout, exit code 1. Not
authenticated → ``{"error": "not_authenticated"}``.
"""

import json
import sys

from music_manager.services.spotify import (
    LIKED_TRACKS_ID,
    LIKED_TRACKS_TITLE,
    count_liked_tracks,
    fetch_user_playlists,
    is_authenticated,
)

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    if not is_authenticated():
        sys.stdout.write(json.dumps({"error": "not_authenticated"}))
        return 1
    try:
        liked_count = count_liked_tracks()
        playlists = fetch_user_playlists()
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": str(exc)[:200]}))
        return 1
    output = [
        {
            "spotify_id": LIKED_TRACKS_ID,
            "title": LIKED_TRACKS_TITLE,
            "nb_tracks": liked_count,
            "picture_url": "",
            "creator": "",
        },
        *playlists,
    ]
    sys.stdout.write(json.dumps(output, ensure_ascii=False))
    return 0
