"""`python -m music_manager recos-playlist-radio "<name>"` — playlist radio.

Same output shape as ``recos-track-radio`` / ``playlist-tracks`` so the
widget plugs it straight into ``PlaylistPreview``. Seeds are the top
tracks of the local Apple Music playlist (by ``play_count``); we fan
out via Deezer's per-track radio then rerank against the user's taste.
"""

import argparse
import json
import os
import sys

from music_manager.core.config import Paths, load_config
from music_manager.pipeline.ecosystem import build_playlist_radio
from music_manager.services.albums import Albums
from music_manager.services.recommendations_store import RecommendationsStore
from music_manager.services.resolver import configure
from music_manager.services.signals import SignalsLog
from music_manager.services.tracks import Tracks

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager recos-playlist-radio")
    parser.add_argument("name", help="Apple Music playlist name")
    parser.add_argument(
        "--persistent-id",
        default="",
        dest="persistent_id",
        help="Apple Music persistent id (optional, for name collisions)",
    )
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
        payload = build_playlist_radio(
            parsed.name,
            tracks_store,
            albums_store,
            recs_store,
            signals=signals,
            persistent_id=parsed.persistent_id,
        )
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": str(exc)[:200]}))
        return 1
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0
