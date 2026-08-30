"""`python -m music_manager recos-artist-radio "<artist>"` — artist radio JSON.

Same shape as ``recos-track-radio`` so the widget reuses ``PlaylistPreview``.
Seed is the artist's name (case-insensitive); we resolve it to a Deezer
artist id via ``/search/artist`` and blend Deezer artist radio + Spotify
top-tracks when authenticated.
"""

import argparse
import json
import os
import sys

from music_manager.core.config import Paths, load_config
from music_manager.pipeline.ecosystem import build_artist_radio
from music_manager.services.albums import Albums
from music_manager.services.recommendations_store import RecommendationsStore
from music_manager.services.resolver import configure
from music_manager.services.signals import SignalsLog
from music_manager.services.tracks import Tracks

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager recos-artist-radio")
    parser.add_argument("artist", help="artist name (case-insensitive)")
    parsed = parser.parse_args(args)

    config = load_config()
    data_root = str(config.get("data_root") or "")
    if not data_root or not os.path.isdir(data_root):
        sys.stdout.write(json.dumps({"error": "data_root_not_configured"}))
        return 1
    paths = Paths(data_root)
    if not os.path.isfile(paths.tracks_path):
        sys.stdout.write(json.dumps({"error": "no_library"}))
        return 1

    configure(str(config.get("language") or "fr"))

    tracks_store = Tracks(paths.tracks_path)
    albums_store = Albums(paths.albums_path)
    recs_store = RecommendationsStore(paths.recommendations_path)
    signals = SignalsLog(paths.signals_log_path)

    try:
        payload = build_artist_radio(
            parsed.artist,
            tracks_store,
            albums_store,
            recs_store,
            signals=signals,
        )
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": str(exc)[:200]}))
        return 1
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0
