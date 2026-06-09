"""`python -m music_manager home` — JSON for the widget landing screen.

Returns the data the Übersicht widget needs to render the "before you
search" view: recently played tracks + the user's playlists.

Output shape::

    {
      "recent": [
        {"title": "...", "artist": "...", "album": "...",
         "apple_id": "...", "cover_url": "..."},
        ...
      ],
      "playlists": [
        {"name": "...", "count": 42},
        ...
      ]
    }

Both arrays may be empty when the data is unavailable (no tracks.json,
Apple Music silent, etc.) — the widget handles that gracefully.
"""

import argparse
import json
import os
import sys

from music_manager.core.config import Paths, load_config
from music_manager.core.io import load_json

# ── Constants ────────────────────────────────────────────────────────────────

_DEFAULT_RECENT_LIMIT = 10
_DEFAULT_PLAYLIST_LIMIT = 30

# Covers live next to the widget JSX so Übersicht can load them with a
# relative URL — `file://` paths to other folders are blocked by WebKit.
_WIDGET_COVERS_DIR = os.path.expanduser(
    "~/Library/Application Support/Übersicht/widgets/music-manager.assets"
)

# Apple Music ships with built-in smart playlists that pollute the list.
# We hide them by exact name match (the widget can offer them if a user
# explicitly opts in later).
_PLAYLIST_BLACKLIST = frozenset(
    {
        "Library",
        "Music",
        "Movies",
        "TV Shows",
        "Podcasts",
        "Audiobooks",
        "Recently Added",
        "Recently Played",
        "Top 25 Most Played",
        "Purchased",
        "Genius",
        "iTunes DJ",
    }
)


# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager home")
    parser.add_argument("--recent-limit", type=int, default=_DEFAULT_RECENT_LIMIT)
    parser.add_argument("--playlist-limit", type=int, default=_DEFAULT_PLAYLIST_LIMIT)
    parsed = parser.parse_args(args)

    payload = {
        "recent": _recent_tracks(max(1, parsed.recent_limit)),
        "playlists": _playlists(max(1, parsed.playlist_limit)),
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


# ── Private Functions ────────────────────────────────────────────────────────


def _recent_tracks(limit: int) -> list[dict]:
    """Return the most recently imported tracks from tracks.json.

    "Recent" means tracks brought into Apple Music via the import pipeline
    (CSV, widget, identify…) — i.e. those carrying an ``imported_at``
    timestamp. Baseline tracks scanned from an existing library are
    excluded; they weren't *imported* by Music Manager.
    """
    config = load_config()
    data_root = str(config.get("data_root") or "")
    if not data_root or not os.path.isdir(data_root):
        return []
    paths = Paths(data_root)
    if not os.path.isfile(paths.tracks_path):
        return []

    tracks = load_json(paths.tracks_path)
    eligible: list[tuple[str, dict]] = []
    for apple_id, entry in tracks.items():
        if not isinstance(entry, dict):
            continue
        imported = str(entry.get("imported_at") or "").strip()
        if not imported:
            continue
        stored_apple_id = str(entry.get("apple_id") or apple_id or "").strip()
        if not stored_apple_id:
            continue
        eligible.append((imported, _format_recent(entry, stored_apple_id)))

    eligible.sort(key=lambda item: item[0], reverse=True)
    return [payload for _imported, payload in eligible[:limit]]


def _format_recent(entry: dict, apple_id: str) -> dict:
    return {
        "title": str(entry.get("title") or ""),
        "artist": str(entry.get("artist") or ""),
        "album": str(entry.get("album") or ""),
        "apple_id": apple_id,
        "cover_url": str(entry.get("cover_url") or ""),
    }


def _playlists(limit: int) -> list[dict]:
    """Return user playlists with cached first-track JPG covers.

    Covers are extracted on first access via iTunesLibrary (PyObjC) and
    stored under ``~/Library/Application Support/Übersicht/widgets/
    music-manager.assets/``. The widget references them with a relative
    URL — Übersicht's WebKit blocks ``file://`` cross-origin loads but
    accepts paths relative to the widget directory.

    Returns each entry with ``cover_filename`` (just the basename) so the
    widget can build a URL like ``url("music-manager.assets/abc.jpg")``.
    """
    try:
        from music_manager.services.apple import (  # noqa: PLC0415
            RECO_FOLDER_NAME,
        )
        from music_manager.services.playlist_covers import (  # noqa: PLC0415
            list_playlists_with_covers,
        )

        raw = list_playlists_with_covers(
            _WIDGET_COVERS_DIR, exclude_folder=RECO_FOLDER_NAME
        )
    except Exception:  # noqa: BLE001
        return []

    items: list[dict] = []
    for entry in raw:
        name = str(entry.get("name") or "").strip()
        if not name or name in _PLAYLIST_BLACKLIST:
            continue
        cover_path = str(entry.get("cover_path") or "")
        # Return the path relative to _WIDGET_COVERS_DIR so the widget can
        # build a URL like `url("music-manager.assets/<relpath>")`. This
        # preserves any subdir (e.g. "custom/chill.jpg") for user overrides.
        if cover_path and cover_path.startswith(_WIDGET_COVERS_DIR + os.sep):
            cover_filename = os.path.relpath(cover_path, _WIDGET_COVERS_DIR)
        elif cover_path:
            cover_filename = os.path.basename(cover_path)
        else:
            cover_filename = ""
        items.append(
            {
                "name": name,
                "count": int(entry.get("count") or 0),
                "cover_filename": cover_filename,
                "is_favorite": bool(entry.get("is_favorite")),
            }
        )
        if len(items) >= limit:
            break
    return items
