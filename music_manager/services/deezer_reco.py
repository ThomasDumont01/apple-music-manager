"""Deezer recommendation endpoints — free public API, no auth required.

Wraps the endpoints the resolver doesn't already cover so the ecosystem
feed can build Spotify-Home style sections without touching Last.fm:

- ``artist/{id}/related`` — similar artists (ranked by Deezer editorial).
- ``artist/{id}/radio`` — 40-track algorithmic radio station.
- ``artist/{id}/top`` — most-played tracks of an artist.
- ``artist/{id}/albums`` — sorted by release date DESC (we filter by age).
- ``album/{id}`` — full tracklist (used to expand a recent album).
- ``track/{id}/radio`` — 40-track "radio de ce titre" (key feature).
- ``chart/{genre_id}/tracks`` — editorial top by genre.
- ``genre`` + ``search/artist`` — lookups from user-friendly names.

Every function returns a list of raw Deezer track dicts (same shape as
``resolver._search_deezer`` output), or a list of artist/album dicts.
Callers (typically ``pipeline.ecosystem``) resolve full ``Track`` objects
via ``resolver.build_track`` + ``resolver.fetch_album_with_cover`` so
covers + ISRCs are populated exactly like a normal Deezer search.
"""

import threading
import urllib.parse
from typing import Any

from music_manager.core.logger import log_event
from music_manager.services.resolver import deezer_get

# ── Constants ────────────────────────────────────────────────────────────────

_ARTIST_ALBUMS_PAGE = 25
_GENRE_CACHE_LOCK = threading.Lock()
_GENRE_CACHE: dict[str, int] | None = None
_ARTIST_ID_CACHE: dict[str, int] = {}
_ARTIST_ID_CACHE_LOCK = threading.Lock()


# ── Artist lookups ───────────────────────────────────────────────────────────


def resolve_artist_id(artist_name: str) -> int:
    """Return the Deezer artist ID for ``artist_name``, or 0 if unknown.

    Uses ``/search/artist`` and picks the top result (Deezer already
    ranks by relevance). Cached in-process to avoid re-querying for the
    same top-artist across multiple sections of the feed.
    """
    if not artist_name:
        return 0
    key = artist_name.strip().lower()
    if not key:
        return 0
    with _ARTIST_ID_CACHE_LOCK:
        cached = _ARTIST_ID_CACHE.get(key)
        if cached is not None:
            return cached
    data = deezer_get(f"/search/artist?q={urllib.parse.quote(artist_name)}&limit=1")
    if not data:
        return 0
    items = data.get("data") or []
    if not isinstance(items, list) or not items:
        return 0
    first = items[0]
    if not isinstance(first, dict):
        return 0
    artist_id = int(first.get("id") or 0)
    with _ARTIST_ID_CACHE_LOCK:
        _ARTIST_ID_CACHE[key] = artist_id
    return artist_id


def artist_related(artist_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    """Return artists related to ``artist_id`` (Deezer editorial ranking)."""
    if not artist_id:
        return []
    data = deezer_get(f"/artist/{artist_id}/related?limit={limit}")
    return _extract_list(data)


def artist_radio(artist_id: int) -> list[dict[str, Any]]:
    """Return ~40 tracks from Deezer's algorithmic artist radio."""
    if not artist_id:
        return []
    data = deezer_get(f"/artist/{artist_id}/radio")
    return _extract_list(data)


def artist_top_tracks(artist_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
    """Return the artist's most-played tracks."""
    if not artist_id:
        return []
    data = deezer_get(f"/artist/{artist_id}/top?limit={limit}")
    return _extract_list(data)


def artist_recent_albums(
    artist_id: int,
    *,
    max_age_days: int = 180,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the artist's albums released within the last ``max_age_days``.

    Sorted by ``release_date`` DESC. Deezer's ``/artist/{id}/albums``
    already returns most-recent-first, so we can stop paging as soon as
    we cross the age threshold.
    """
    if not artist_id:
        return []
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    cutoff = datetime.now(UTC).date() - timedelta(days=max_age_days)
    kept: list[dict[str, Any]] = []
    index = 0
    while True:
        data = deezer_get(
            f"/artist/{artist_id}/albums?limit={_ARTIST_ALBUMS_PAGE}&index={index}"
        )
        if not data:
            break
        items = _extract_list(data)
        if not items:
            break
        stop = False
        for album in items:
            release_date = str(album.get("release_date") or "")[:10]
            if release_date:
                try:
                    released = datetime.strptime(release_date, "%Y-%m-%d").date()
                except ValueError:
                    released = None
                if released is not None and released < cutoff:
                    stop = True
                    break
            kept.append(album)
            if len(kept) >= limit:
                stop = True
                break
        if stop or not data.get("next"):
            break
        index += _ARTIST_ALBUMS_PAGE
    return kept


# ── Album ────────────────────────────────────────────────────────────────────


def album_tracks(album_id: int) -> list[dict[str, Any]]:
    """Return the tracklist of a Deezer album.

    Track dicts don't include an ``album`` sub-object (Deezer inlines the
    album metadata at the top level), so we synthesise it so downstream
    ``resolver.build_track`` gets what it expects.
    """
    if not album_id:
        return []
    data = deezer_get(f"/album/{album_id}")
    if not data:
        return []
    tracks_container = data.get("tracks") or {}
    if not isinstance(tracks_container, dict):
        return []
    raw = tracks_container.get("data") or []
    if not isinstance(raw, list):
        return []
    album_stub = {
        "id": data.get("id"),
        "title": data.get("title", ""),
        "cover_medium": data.get("cover_medium", ""),
        "cover": data.get("cover", ""),
    }
    prepared: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        item.setdefault("album", album_stub)
        prepared.append(item)
    return prepared


# ── Track radio (key feature: "Radio de ce titre") ───────────────────────────


def track_radio(track_id: int) -> list[dict[str, Any]]:
    """Return an approximate "track radio" derived from the track's artist.

    Deezer's ``/track/{id}/radio`` endpoint is undocumented and returns
    nothing in practice. We fall back to the track's primary artist
    radio: fetch ``/track/{id}`` to grab ``artist.id`` then delegate to
    ``/artist/{aid}/radio``. Not as tight as a per-track model, but good
    enough — our reranking layer re-focuses the result on the user's
    taste anyway.
    """
    if not track_id:
        return []
    data = deezer_get(f"/track/{track_id}")
    if not data or "error" in data:
        return []
    artist_id = int((data.get("artist") or {}).get("id") or 0)
    if not artist_id:
        return []
    return artist_radio(artist_id)


# ── Genre charts ─────────────────────────────────────────────────────────────


def resolve_genre_id(genre_name: str) -> int:
    """Return the Deezer genre ID for a case-insensitive name, or 0.

    The ``/genre`` endpoint is stable (returns Deezer's editorial genre
    list). Cached once per process.
    """
    if not genre_name:
        return 0
    key = genre_name.strip().lower()
    if not key:
        return 0
    catalog = _load_genre_catalog()
    return catalog.get(key, 0)


def genre_charts(genre_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    """Return the editorial chart-top tracks for a Deezer genre.

    Note: Deezer genre id 0 == "All", still useful as a global chart
    fallback for cold-start.
    """
    data = deezer_get(f"/chart/{genre_id}/tracks?limit={limit}")
    return _extract_list(data)


# ── Private helpers ──────────────────────────────────────────────────────────


def _extract_list(data: dict | None) -> list[dict[str, Any]]:
    """Return ``data["data"]`` filtered to dicts, or ``[]``."""
    if not data:
        return []
    items = data.get("data")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _load_genre_catalog() -> dict[str, int]:
    """Load Deezer's editorial genre list once, keyed by lowercase name."""
    global _GENRE_CACHE  # noqa: PLW0603
    with _GENRE_CACHE_LOCK:
        if _GENRE_CACHE is not None:
            return _GENRE_CACHE
    data = deezer_get("/genre")
    catalog: dict[str, int] = {}
    if data:
        for item in _extract_list(data):
            name = str(item.get("name") or "").strip().lower()
            gid = int(item.get("id") or 0)
            if name and gid > 0:
                catalog[name] = gid
    with _GENRE_CACHE_LOCK:
        _GENRE_CACHE = catalog
    if not catalog:
        log_event("deezer_reco_genre_catalog_empty")
    return catalog


def _reset_caches_for_tests() -> None:
    """Test hook: forget the module-level artist ID + genre catalog caches."""
    global _GENRE_CACHE  # noqa: PLW0603
    with _GENRE_CACHE_LOCK:
        _GENRE_CACHE = None
    with _ARTIST_ID_CACHE_LOCK:
        _ARTIST_ID_CACHE.clear()
