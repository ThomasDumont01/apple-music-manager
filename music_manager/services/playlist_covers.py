"""Apple Music playlists + first-track cover extraction.

Used by the widget's landing screen. Reads playlists via iTunesLibrary.
framework (one PyObjC call, no AppleScript).

Cover selection — first match wins:
1. ``<covers_dir>/custom/<slug>.jpg`` — user-supplied override (preferred).
   This lets the user mirror their custom Apple Music playlist artwork,
   which Apple does NOT expose through any public API.
2. ``<covers_dir>/<persistent_id>.jpg`` — auto-extracted from the
   playlist's first track that has artwork.

Filters: only "regular" user-created playlists (kind=0, distinguished=0,
not master) are returned — Apple's built-in smart playlists (Library,
Recently Played…) are hidden.
"""

import os
import re

# ── Constants ────────────────────────────────────────────────────────────────

# ITLibPlaylistKind enum: 0 = Regular, 1 = Smart, 2 = Genius, etc.
# Both regular AND smart playlists count as "user playlists" as long as their
# distinguishedKind is None (= not a built-in like "Library" or "Music").
_PLAYLIST_KINDS_KEPT = (0, 1)
# ITLibDistinguishedPlaylistKind: 0 = None (user-created), others are built-ins.
_DISTINGUISHED_NONE = 0


# ── Entry point ──────────────────────────────────────────────────────────────


def list_playlists_with_covers(covers_dir: str) -> list[dict]:
    """Return user playlists + first-track JPG cover (cached on disk).

    Each item: ``{"name": str, "count": int, "cover_path": str | ""}``.
    The cover path is empty when the playlist has no track with artwork.

    ``covers_dir`` is created on demand. Cached files are reused if present.
    Errors loading the iTunesLibrary framework return an empty list rather
    than raising — the widget can still render the landing screen.
    """
    try:
        import objc  # noqa: PLC0415
    except ImportError:
        return []

    try:
        objc.loadBundle(  # type: ignore[attr-defined]
            "iTunesLibrary",
            {},
            bundle_path="/System/Library/Frameworks/iTunesLibrary.framework",
        )
        ITLibrary = objc.lookUpClass("ITLibrary")  # type: ignore[attr-defined]
        library = ITLibrary.alloc().initWithAPIVersion_error_("1.1", None)
        if library is None:
            return []
    except Exception:  # noqa: BLE001
        return []

    os.makedirs(covers_dir, exist_ok=True)
    custom_dir = os.path.join(covers_dir, "custom")
    custom_index = _index_custom_covers(custom_dir)

    result: list[dict] = []
    for playlist in library.allPlaylists():
        try:
            if int(playlist.kind()) not in _PLAYLIST_KINDS_KEPT:
                continue
            if int(playlist.distinguishedKind()) != _DISTINGUISHED_NONE:
                continue
            # Skip the library root (named "Library" / "Bibliothèque" / …):
            # it's a user-kind, undistinguished playlist but represents the
            # whole library, not a user-created selection.
            if hasattr(playlist, "isMaster") and bool(playlist.isMaster()):
                continue
            name = str(playlist.name() or "").strip()
            if not name:
                continue
            # Only show playlists whose cover the user explicitly provided
            # in the `custom/` directory. The auto-extracted first-track
            # cover was misleading (Apple doesn't expose custom artwork),
            # so we prefer to hide rather than show something wrong.
            cover_path = custom_index.get(_slug(name), "")
            if not cover_path:
                continue
            items = playlist.items()
            count = len(items) if items else 0
            result.append({"name": name, "count": count, "cover_path": cover_path})
        except Exception:  # noqa: BLE001
            continue

    return result


# ── Private Functions ────────────────────────────────────────────────────────


def _slug(name: str) -> str:
    """Sluggify a playlist name: lowercase, non-alphanum → '-'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _index_custom_covers(custom_dir: str) -> dict[str, str]:
    """Map slug → absolute path for every image present in ``custom_dir``."""
    if not os.path.isdir(custom_dir):
        return {}
    index: dict[str, str] = {}
    for entry in os.listdir(custom_dir):
        path = os.path.join(custom_dir, entry)
        if not os.path.isfile(path):
            continue
        base, ext = os.path.splitext(entry)
        if ext.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        index[_slug(base)] = path
    return index


def _ensure_cover(playlist, items, covers_dir: str) -> str:
    """Return the cached JPG path for this playlist, extracting it if needed."""
    if not items or len(items) == 0:
        return ""

    pl_id = format(int(playlist.persistentID()), "016X")
    dest = os.path.join(covers_dir, f"{pl_id}.jpg")
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return dest

    # Walk the first few tracks: the very first track may lack artwork.
    for item in list(items)[:5]:
        try:
            if not bool(item.hasArtworkAvailable()):
                continue
            artwork = item.artwork()
            if artwork is None:
                continue
            data = artwork.imageData()
            if data is None:
                continue
            raw = bytes(data)
            if not raw:
                continue
            with open(dest, "wb") as out:
                out.write(raw)
            return dest
        except Exception:  # noqa: BLE001
            continue

    return ""
