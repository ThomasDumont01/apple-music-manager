"""`python -m music_manager recos-feed` — Spotify-Home style feed as JSON.

Consumed by the Übersicht widget's "Pour toi" tab. Output shape::

    {
      "generated_at": "2026-07-13T12:34:56+00:00",
      "cache_hit": true,
      "sections": [
        {
          "id": "recent",
          "title": "Basé sur tes écoutes récentes",
          "subtitle": "...",
          "layout": "subcards" | "row",
          "subcards": [ {id, title, subtitle, seed, tracks: [...]}, ... ],
          "tracks":   [ ... ]      // when layout == "row"
        },
        ...
      ],
      "errors": {"section_id": "message", ...}
    }

Each track dict mirrors the ``search`` command output (ISRC, title,
artist, album, cover_url, preview_url, deezer_id, in_library, apple_id).

Errors during a specific section are trapped and reported in ``errors``
— the rest of the feed still ships so the widget can render whatever
succeeded.
"""

import argparse
import json
import os
import sys

from music_manager.core.config import Paths, load_config
from music_manager.pipeline.ecosystem import DEFAULT_SECTIONS, build_feed
from music_manager.services.albums import Albums
from music_manager.services.recommendations_store import RecommendationsStore
from music_manager.services.resolver import configure
from music_manager.services.signals import SignalsLog
from music_manager.services.tracks import Tracks

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager recos-feed")
    parser.add_argument(
        "--sections",
        default="all",
        help="comma-separated section ids (default: all)",
    )
    parser.add_argument(
        "--max-per-section",
        type=int,
        default=10,
        dest="max_per_section",
        help="max tracks per section / sub-card (default 10)",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        dest="force_refresh",
        help="bypass the 30 min disk cache",
    )
    parsed = parser.parse_args(args)

    config = load_config()
    data_root = str(config.get("data_root") or "")
    if not data_root or not os.path.isdir(data_root):
        sys.stdout.write(json.dumps({"error": "data_root_not_configured"}))
        return 1
    paths = Paths(data_root)

    if not os.path.isfile(paths.tracks_path):
        sys.stdout.write(
            json.dumps(
                {
                    "generated_at": "",
                    "sections": [],
                    "errors": {"_bootstrap": "no library yet"},
                    "cache_hit": False,
                }
            )
        )
        return 0

    configure(str(config.get("language") or "fr"))

    sections = _parse_sections(parsed.sections)
    tracks_store = Tracks(paths.tracks_path)
    albums_store = Albums(paths.albums_path)
    recs_store = RecommendationsStore(paths.recommendations_path)
    signals = SignalsLog(paths.signals_log_path)

    try:
        payload = build_feed(
            paths,
            tracks_store,
            albums_store,
            recs_store,
            signals=signals,
            sections=sections,
            max_per_section=max(1, min(30, parsed.max_per_section)),
            force_refresh=parsed.force_refresh,
        )
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": str(exc)[:200]}))
        return 1
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


# ── Private Functions ────────────────────────────────────────────────────────


def _parse_sections(raw: str) -> list[str]:
    """Turn ``--sections`` into a validated list.

    ``all`` (default) means every default section in canonical order.
    Comma-separated names are filtered against the whitelist so a typo
    doesn't crash the pipeline.
    """
    if not raw or raw.strip().lower() == "all":
        return list(DEFAULT_SECTIONS)
    wanted = [item.strip() for item in raw.split(",") if item.strip()]
    return [name for name in wanted if name in DEFAULT_SECTIONS]
