"""Apple Music playlists + auto-extracted covers via iTunesLibrary.

Used by the widget's landing screen. Reads playlists via iTunesLibrary
framework (one PyObjC call, no AppleScript) and extracts each playlist's
custom artwork (the cover the user set in Music.app) to a disk cache the
widget can serve as a relative URL.

Apple Music = single source of truth. The widget no longer accepts user
overrides — to change a cover, edit it in Apple Music itself and the next
scan picks it up (UUID-based cache invalidation).

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

# Mapping ITLibArtworkFormat → extension (cf. iTunesLibrary.h).
_ARTWORK_EXT = {1: ".bmp", 2: ".jpg", 3: ".jp2", 4: ".gif", 5: ".png", 6: ".bmp", 7: ".tiff"}


# ── Entry point ──────────────────────────────────────────────────────────────


def list_playlists_with_covers(
    covers_dir: str, *, exclude_folder: str | None = None
) -> list[dict]:
    """Return user playlists + Apple-Music-extracted artwork (cached on disk).

    Each item: ``{"name": str, "count": int, "cover_path": str | "",
    "is_favorite": bool}``. ``cover_path`` is empty when the playlist has no
    artwork set in Apple Music.

    If ``exclude_folder`` is given, playlists whose parent folder bears that
    name are omitted from the result. Used to hide the ``for me`` recommendation
    sub-playlists from the widget's "user playlists" view.

    ``covers_dir`` is created on demand; the ``auto/`` sub-folder holds the
    extracted images. Cache files are named ``<persistentID>_<artworkUUID>.ext``
    so the next call detects when the user changes the cover in Apple Music
    (the UUID rotates) and replaces the stale file.

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
    auto_dir = os.path.join(covers_dir, "auto")
    os.makedirs(auto_dir, exist_ok=True)

    # Apple Music exposes a "favorite" flag on playlists via AppleScript
    # (Music 1.4 / macOS Sonoma+). Batched once, then cross-referenced by name.
    # Silent failure on older macOS.
    favorited_names = _fetch_favorited_playlists()

    # Resolve persistentID → name for ALL playlists first, so we can match each
    # playlist's parentID against ``exclude_folder``. Folders are themselves
    # playlists in iTunesLibrary (kind=4 / kind=3 depending on version), so we
    # don't filter them out at the lookup stage.
    all_playlists = list(library.allPlaylists())
    id_to_name: dict[int, str] = {}
    if exclude_folder:
        for playlist in all_playlists:
            try:
                pid = int(playlist.persistentID())
                pname = str(playlist.name() or "").strip()
                if pname:
                    id_to_name[pid] = pname
            except Exception:  # noqa: BLE001
                continue

    result: list[dict] = []
    for playlist in all_playlists:
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
            if exclude_folder:
                if name == exclude_folder:
                    continue
                if _parent_name(playlist, id_to_name) == exclude_folder:
                    continue
            cover_path = _extract_playlist_artwork(playlist, auto_dir)
            items = playlist.items()
            count = len(items) if items else 0
            is_favorite = (
                name in favorited_names
                or _is_favorited(playlist)
            )
            result.append(
                {
                    "name": name,
                    "count": count,
                    "cover_path": cover_path,
                    "is_favorite": is_favorite,
                }
            )
        except Exception:  # noqa: BLE001
            continue

    # Tri : favoris (Apple Music heart, ou nom suggérant un "liked" / "favori")
    # d'abord, puis alphabétique case-insensitive.
    result.sort(
        key=lambda p: (
            0 if p.get("is_favorite") or _looks_like_liked(p.get("name", "")) else 1,
            (p.get("name") or "").lower(),
        )
    )
    return result


# ── Private Functions ────────────────────────────────────────────────────────


_LIKED_KEYWORDS = frozenset(
    {
        "like",
        "liked",
        "likes",
        "likés",
        "like-songs",
        "liked-songs",
        "loved",
        "loved-tracks",
        "favori",
        "favoris",
        "favorite",
        "favorites",
        "favourite",
        "favourites",
        "❤",
        "♥",
        "titres-likes",
        "titres-likés",
        "titres-aimes",
        "titres-aimés",
    }
)


def _parent_name(playlist: object, id_to_name: dict[int, str]) -> str:
    """Return the parent folder's name, or ``""`` if the playlist is at root.

    Different macOS versions expose the parent ID via different selectors —
    we try the known shapes silently.
    """
    for attr in ("parentID", "parentId"):
        if not hasattr(playlist, attr):
            continue
        try:
            raw = getattr(playlist, attr)()
        except Exception:  # noqa: BLE001
            continue
        if raw is None:
            return ""
        try:
            parent_id = int(raw)
        except (TypeError, ValueError):
            continue
        if parent_id == 0:
            return ""
        return id_to_name.get(parent_id, "")
    return ""


def _looks_like_liked(name: str) -> bool:
    """True if the name suggests it's the user's 'liked / favorites' playlist."""
    if not name:
        return False
    stripped = name.strip()
    if stripped.startswith("❤") or stripped.startswith("♥"):
        return True
    return _slug(stripped) in _LIKED_KEYWORDS


def _extract_playlist_artwork(playlist: object, auto_dir: str) -> str:
    """Extract the playlist's Apple Music artwork to ``auto/<pid>_<uuid>.<ext>``.

    Returns the absolute path or ``""`` if no artwork is set. The UUID in the
    filename invalidates the cache when the user changes the cover in Apple
    Music — the next scan extracts the new one and purges the stale file.
    """
    try:
        if not hasattr(playlist, "hasArtworkAvailable"):
            return ""
        if not bool(playlist.hasArtworkAvailable()):  # type: ignore[attr-defined]
            return ""
        if not hasattr(playlist, "artwork"):
            return ""
        art = playlist.artwork()  # type: ignore[attr-defined]
        if art is None:
            return ""
        try:
            pid = format(int(playlist.persistentID()), "016X")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return ""
        uuid = ""
        try:
            uuid = str(playlist.artworkUUID() or "")[:8]  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        ext = ".png"
        try:
            fmt = int(art.imageDataFormat())
            ext = _ARTWORK_EXT.get(fmt, ".png")
        except Exception:  # noqa: BLE001
            pass

        filename = f"{pid}_{uuid}{ext}" if uuid else f"{pid}{ext}"
        full_path = os.path.join(auto_dir, filename)

        if not os.path.isfile(full_path):
            data = art.imageData()
            if not data or not len(data):
                return ""
            with open(full_path, "wb") as handle:
                handle.write(bytes(data))
            # Nettoie les anciens caches pour ce persistentID (UUID périmé).
            try:
                for entry in os.listdir(auto_dir):
                    if entry.startswith(pid) and entry != filename:
                        try:
                            os.remove(os.path.join(auto_dir, entry))
                        except OSError:
                            continue
            except OSError:
                pass
        return full_path
    except Exception:  # noqa: BLE001
        return ""


def _fetch_favorited_playlists() -> set[str]:
    """Names of playlists marked as 'Favorite' in Apple Music (Sonoma+).

    macOS Sonoma 14.4+ Music.app exposes ``favorited`` on user playlists. On
    older macOS the property doesn't exist — the AppleScript ``try`` block
    swallows the error and the set stays empty. Sorting then falls back to
    the name-based heuristic.
    """
    script = (
        'tell application "Music"\n'
        '\tset output to ""\n'
        "\trepeat with pl in user playlists\n"
        "\t\ttry\n"
        "\t\t\tif favorited of pl is true then\n"
        '\t\t\t\tset output to output & (name of pl) & "‖"\n'
        "\t\t\tend if\n"
        "\t\tend try\n"
        "\tend repeat\n"
        "\treturn output\n"
        "end tell\n"
    )
    try:
        from music_manager.services.apple import run_applescript  # noqa: PLC0415

        raw = run_applescript(script)
    except Exception:  # noqa: BLE001
        return set()
    if not raw:
        return set()
    return {name.strip() for name in raw.split("‖") if name.strip()}


def _is_favorited(playlist: object) -> bool:
    """Best-effort: read Apple Music's "favorite" flag via PyObjC if exposed.

    The exact selector varies between macOS versions (Music 1.x bumps add new
    selectors). We try a few known names and fall back to False — sorting
    then relies on ``_looks_like_liked`` for the user's named-as-liked playlists.
    """
    for attr in ("isFavorite", "isFavorited", "favorited", "loved", "isLoved"):
        if not hasattr(playlist, attr):
            continue
        try:
            value = getattr(playlist, attr)()
        except Exception:  # noqa: BLE001
            continue
        if isinstance(value, bool):
            return value
    return False


def _slug(name: str) -> str:
    """Sluggify a playlist name: lowercase, non-alphanum → '-'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
