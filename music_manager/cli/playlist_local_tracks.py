"""`python -m music_manager playlist-local-tracks <NAME>` — Apple Music playlist tracks.

Reads the named Apple Music playlist via iTunesLibrary.framework and returns
its tracks in a shape compatible with the Übersicht widget's
``PlaylistPreview`` component. Each track carries ``apple_id`` + ``title`` +
``artist`` from Apple's library, plus ``isrc`` + ``cover_url`` from the
project's ``tracks.json`` when available (i.e. tracks already touched by
Music Manager). Tracks are always marked ``in_library: true``.

Output schema (stable, widget-consumed)::

    {"name": "<playlist name>", "creator": "", "nb_tracks": N,
     "cover_url": "", "tracks": [{...}], "skipped_no_isrc": 0}

Errors: ``{"error": "not_found"}`` if the playlist doesn't exist or the
framework call failed.
"""

import argparse
import json
import os
import sys

from music_manager.core.config import Paths, load_config
from music_manager.core.io import load_json

# ── Constants ────────────────────────────────────────────────────────────────

_ITUNES_LIB_BUNDLE = "/System/Library/Frameworks/iTunesLibrary.framework"


# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager playlist-local-tracks")
    parser.add_argument("name", help="Apple Music playlist name")
    parser.add_argument(
        "--persistent-id",
        default="",
        help=(
            "16-char hex Apple Music persistentID — disambiguates same-named "
            "playlists. Falls back to name lookup when empty."
        ),
    )
    parsed = parser.parse_args(args)

    items = (
        _load_playlist_items(parsed.name, parsed.persistent_id)
        if parsed.persistent_id
        else _load_playlist_items(parsed.name)
    )
    if items is None:
        sys.stdout.write(json.dumps({"error": "not_found"}))
        return 1

    library_index = _load_tracks_index()
    enriched: list[dict] = []
    for item in items:
        apple_id = item.get("apple_id", "")
        meta = library_index.get(apple_id, {}) if apple_id else {}
        enriched.append(
            {
                "apple_id": apple_id,
                "isrc": str(meta.get("isrc") or "").strip().upper(),
                "title": item.get("title", "") or str(meta.get("title") or ""),
                "artist": item.get("artist", "") or str(meta.get("artist") or ""),
                "cover_url": str(meta.get("cover_url") or ""),
                "preview_url": "",
                "in_library": True,
            }
        )

    payload = {
        "name": parsed.name,
        "creator": "",
        "nb_tracks": len(enriched),
        "cover_url": "",
        "tracks": enriched,
        "skipped_no_isrc": 0,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


# ── Private Functions ────────────────────────────────────────────────────────


def _load_tracks_index() -> dict[str, dict]:
    """Return ``apple_id → track metadata`` from tracks.json."""
    config = load_config()
    data_root = str(config.get("data_root") or "")
    if not data_root or not os.path.isdir(data_root):
        return {}
    paths = Paths(data_root)
    if not os.path.isfile(paths.tracks_path):
        return {}
    data = load_json(paths.tracks_path)
    return {key: entry for key, entry in data.items() if isinstance(entry, dict)}


def _load_playlist_items(name: str, persistent_id: str = "") -> list[dict] | None:
    """Return [{apple_id, title, artist}] for the playlist via iTunesLibrary.

    Resolution: if ``persistent_id`` is provided, match on it (exact, unique).
    Otherwise fall back to case-insensitive name match — ambiguous when two
    playlists share a name.

    Returns None if iTunesLibrary is unavailable or the playlist doesn't exist.
    """
    try:
        import objc  # noqa: PLC0415
    except ImportError:
        return None

    try:
        objc.loadBundle(  # type: ignore[attr-defined]
            "iTunesLibrary",
            {},
            bundle_path=_ITUNES_LIB_BUNDLE,
        )
        ITLibrary = objc.lookUpClass("ITLibrary")  # type: ignore[attr-defined]
        library = ITLibrary.alloc().initWithAPIVersion_error_("1.1", None)
        if library is None:
            return None
    except Exception:  # noqa: BLE001
        return None

    target_pid = (persistent_id or "").strip().upper()
    target_name = (name or "").strip().lower()
    for playlist in library.allPlaylists():
        try:
            if target_pid:
                try:
                    pl_pid = format(int(playlist.persistentID()), "016X")
                except Exception:  # noqa: BLE001
                    continue
                if pl_pid != target_pid:
                    continue
            else:
                pl_name = str(playlist.name() or "").strip()
                if pl_name.lower() != target_name:
                    continue
            items = playlist.items() or []
            tracks: list[dict] = []
            for item in items:
                try:
                    persistent_id = item.persistentID()
                    apple_id = format(persistent_id, "016X")
                    title = str(item.title() or "")
                    artist_obj = item.artist()
                    artist = str(artist_obj.name() or "") if artist_obj else ""
                    tracks.append(
                        {
                            "apple_id": apple_id,
                            "title": title,
                            "artist": artist,
                        }
                    )
                except Exception:  # noqa: BLE001
                    continue
            return tracks
        except Exception:  # noqa: BLE001
            continue
    return None
