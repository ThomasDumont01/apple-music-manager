"""`python -m music_manager recos-mixes` — landing grid for the "Pour toi" tab.

Fast (~1-2 s cold, cached 6 h). Returns groups of clickable mix cards
without resolving any track — the widget opens a mix on click by calling
``recos-mix-tracks <kind> <value>``.

Output shape::

    {
      "generated_at": "...",
      "cache_hit": true,
      "groups": [
        {
          "id": "recent",
          "title": "Basé sur tes écoutes récentes",
          "cards": [
            {"kind": "track", "value": "FRABC...", "title": "Radio ...",
             "subtitle": "Artist", "cover_url": "https://..."},
            ...
          ]
        },
        {"id": "artists", "title": "Vos mix par artiste", "cards": [...]},
        {"id": "genres",  "title": "Vos mix par genre",   "cards": [...]},
        {"id": "decades", "title": "Vos mix par décennie", "cards": [...]},
        {"id": "moods",   "title": "Vos Mood Mixes",       "cards": [...]}
      ]
    }
"""

import argparse
import json
import os
import sys

from music_manager.core.config import Paths, load_config
from music_manager.pipeline.ecosystem import build_mixes_index
from music_manager.services.resolver import configure
from music_manager.services.signals import SignalsLog
from music_manager.services.tracks import Tracks

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager recos-mixes")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        dest="force_refresh",
        help="bypass the 6 h disk cache",
    )
    parsed = parser.parse_args(args)

    config = load_config()
    data_root = str(config.get("data_root") or "")
    if not data_root or not os.path.isdir(data_root):
        sys.stdout.write(json.dumps({"error": "data_root_not_configured"}))
        return 1
    paths = Paths(data_root)
    if not os.path.isfile(paths.tracks_path):
        sys.stdout.write(json.dumps({"generated_at": "", "groups": [], "cache_hit": False}))
        return 0

    configure(str(config.get("language") or "fr"))
    tracks_store = Tracks(paths.tracks_path)
    signals = SignalsLog(paths.signals_log_path)

    try:
        payload = build_mixes_index(
            paths, tracks_store, signals=signals, force_refresh=parsed.force_refresh
        )
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": str(exc)[:200]}))
        return 1
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0
