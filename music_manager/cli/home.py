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
        {"name": "...", "persistent_id": "ABCD...", "count": 42},
        ...
      ]
    }

Both arrays may be empty when the data is unavailable (no tracks.json,
Apple Music silent, etc.) — the widget handles that gracefully.
"""

import argparse
import functools
import json
import os
import plistlib
import subprocess
import sys
import urllib.parse

from music_manager.core.config import Paths, load_config
from music_manager.core.io import load_json

# ── Constants ────────────────────────────────────────────────────────────────

_DEFAULT_RECENT_LIMIT = 10
_DEFAULT_PLAYLIST_LIMIT = 30

# Covers live next to the widget JSX so Übersicht can load them with a
# relative URL — `file://` paths to other folders are blocked by WebKit.
_ASSETS_DIRNAME = "music-manager.assets"
_DEFAULT_UBERSICHT_WIDGETS_DIR = os.path.expanduser(
    "~/Library/Application Support/Übersicht/widgets"
)
_UBERSICHT_DOMAIN = "tracesOf.Uebersicht"


def _widget_covers_dir() -> str:
    """Folder the widget reads its cached covers from.

    ``MUSIC_MANAGER_WIDGET_ASSETS_DIR`` is the escape hatch for setups the
    discovery can't see; otherwise we follow Übersicht's own configuration
    and fall back to the folder it ships with.
    """
    override = os.environ.get("MUSIC_MANAGER_WIDGET_ASSETS_DIR")
    if override:
        return override
    widgets_dir = _ubersicht_widget_dir() or _DEFAULT_UBERSICHT_WIDGETS_DIR
    return os.path.join(widgets_dir, _ASSETS_DIRNAME)


@functools.lru_cache(maxsize=1)
def _ubersicht_widget_dir() -> str:
    """Übersicht's configured widgets folder, or "" when undiscoverable.

    Users can move that folder, and Übersicht records it as an
    NSKeyedArchiver-encoded NSURL nested inside its preferences domain — not
    as a plain path. The archive keeps the URL as a bare string in
    ``$objects``, so stdlib plistlib is enough to read it back.
    """
    try:
        raw = subprocess.run(
            ["/usr/bin/defaults", "export", _UBERSICHT_DOMAIN, "-"],
            capture_output=True,
            timeout=5,
            check=False,
        ).stdout
        if not raw:
            return ""
        archived = plistlib.loads(raw).get("widgetDirectory")
        if not isinstance(archived, bytes):
            return ""
        for item in plistlib.loads(archived).get("$objects") or []:
            if isinstance(item, str) and item.startswith("file://"):
                path = urllib.parse.unquote(urllib.parse.urlparse(item).path)
                return path.rstrip("/")
    except (OSError, ValueError, plistlib.InvalidFileException, subprocess.SubprocessError):
        return ""
    return ""


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
        # ISRC exposed so the widget can trigger "Radio de ce titre" via
        # ``recos-track-radio <ISRC>`` without a round-trip to look it up.
        "isrc": str(entry.get("isrc") or "").upper(),
    }


def _playlists(limit: int) -> list[dict]:
    """Return user playlists with cached local covers.

    Covers are extracted on first access via iTunesLibrary (PyObjC) and
    stored under ``~/Library/Application Support/Übersicht/widgets/
    music-manager.assets/``. The widget references them with a relative
    URL — Übersicht's WebKit blocks ``file://`` cross-origin loads but
    accepts paths relative to the widget directory.

    Returns each entry with ``cover_filename`` and ``mosaic_cover_filenames``
    so the widget can build URLs like ``music-manager.assets/abc.jpg``.
    """
    try:
        from music_manager.services.playlist_covers import (  # noqa: PLC0415
            list_playlists_with_covers,
        )

        # Exclut le dossier "for me" (recos Apple Music) : ces playlists
        # ne doivent jamais apparaître dans le widget, même via "Voir tout".
        raw = list_playlists_with_covers(_widget_covers_dir(), exclude_folder="for me")
    except Exception:  # noqa: BLE001
        return []

    items: list[dict] = []
    for entry in raw:
        name = str(entry.get("name") or "").strip()
        if not name or name in _PLAYLIST_BLACKLIST:
            continue
        cover_path = str(entry.get("cover_path") or "")
        # Return the path relative to _widget_covers_dir() so the widget can
        # build a URL like `url("music-manager.assets/<relpath>")`. This
        # preserves any subdir (e.g. "custom/chill.jpg") for user overrides.
        if cover_path and cover_path.startswith(_widget_covers_dir() + os.sep):
            cover_filename = os.path.relpath(cover_path, _widget_covers_dir())
        elif cover_path:
            cover_filename = os.path.basename(cover_path)
        else:
            cover_filename = ""
        mosaic_cover_filenames = [
            _asset_filename(path)
            for path in entry.get("mosaic_cover_paths") or []
            if _asset_filename(path)
        ][:4]
        items.append(
            {
                "name": name,
                "persistent_id": str(entry.get("persistent_id") or ""),
                "count": int(entry.get("count") or 0),
                "cover_filename": cover_filename,
                "mosaic_cover_filenames": mosaic_cover_filenames,
                "is_favorite": bool(entry.get("is_favorite")),
            }
        )
        if len(items) >= limit:
            break
    return items


def _asset_filename(path: object) -> str:
    raw = str(path or "")
    if not raw:
        return ""
    if raw.startswith(_widget_covers_dir() + os.sep):
        return os.path.relpath(raw, _widget_covers_dir())
    return os.path.basename(raw)
