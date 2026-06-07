"""`python -m music_manager search-playlists "query" [--limit N]` — JSON stdout.

Used by the Übersicht widget when the user toggles into "Playlists" mode.
Output schema (stable — the widget parses it directly)::

    [
      {"deezer_id": 908622995, "title": "Lofi Hip Hop", "nb_tracks": 42,
       "picture_url": "https://...", "creator": "deezer"},
      ...
    ]

Errors are surfaced as ``{"error": "..."}`` on stdout, exit code 1.
"""

import argparse
import json
import sys

from music_manager.services.resolver import configure, search_deezer_playlists

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    """CLI entry. Returns the exit code."""
    parser = argparse.ArgumentParser(prog="music_manager search-playlists")
    parser.add_argument("query", help="free-text search query")
    parser.add_argument(
        "--limit", type=int, default=10, help="max results (default 10, capped at 50)"
    )
    parsed = parser.parse_args(args)

    configure("fr")

    try:
        raw = search_deezer_playlists(parsed.query, parsed.limit)
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": str(exc)[:200]}))
        return 1

    results = [_format(item) for item in raw if isinstance(item, dict)]
    sys.stdout.write(json.dumps(results, ensure_ascii=False))
    return 0


# ── Private Functions ────────────────────────────────────────────────────────


def _format(item: dict) -> dict:
    """Project the Deezer playlist dict onto the stable widget schema."""
    user_obj = item.get("user") or {}
    picture = item.get("picture_medium") or item.get("picture") or ""
    return {
        "deezer_id": int(item.get("id") or 0),
        "title": str(item.get("title") or ""),
        "nb_tracks": int(item.get("nb_tracks") or 0),
        "picture_url": str(picture),
        "creator": str(user_obj.get("name") or ""),
    }
