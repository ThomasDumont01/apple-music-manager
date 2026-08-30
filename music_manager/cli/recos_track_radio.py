"""`python -m music_manager recos-track-radio <ISRC>` — track radio JSON.

Same output shape as ``playlist-tracks`` so the widget can pipe it
straight into its existing ``PlaylistPreview`` component::

    {"name": "Radio de <title>", "creator": "<artist>",
     "nb_tracks": 25, "cover_url": "https://...", "cover_thumb": "https://...",
     "tracks": [{isrc, title, artist, cover_url, preview_url,
                 in_library, apple_id, deezer_id, ...}, ...],
     "skipped": 0}

Requires the ISRC to be present in ``tracks.json`` (the widget only
surfaces this action on library tracks or recent imports).
"""

import argparse
import json
import os
import sys

from music_manager.core.config import Paths, load_config
from music_manager.pipeline.ecosystem import build_track_radio
from music_manager.services.albums import Albums
from music_manager.services.recommendations_store import RecommendationsStore
from music_manager.services.resolver import configure
from music_manager.services.signals import SignalsLog
from music_manager.services.tracks import Tracks

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager recos-track-radio")
    parser.add_argument(
        "isrc",
        nargs="?",
        default="",
        help="seed track ISRC (skips the library lookup when empty)",
    )
    parser.add_argument("--deezer-id", type=int, default=0, dest="deezer_id")
    parser.add_argument("--title", default="")
    parser.add_argument("--artist", default="")
    parser.add_argument("--cover-url", default="", dest="cover_url")
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
        payload = build_track_radio(
            parsed.isrc.strip().upper(),
            tracks_store,
            albums_store,
            recs_store,
            signals=signals,
            deezer_id=parsed.deezer_id,
            seed_title=parsed.title,
            seed_artist=parsed.artist,
            cover_url=parsed.cover_url,
        )
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": str(exc)[:200]}))
        return 1
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0
