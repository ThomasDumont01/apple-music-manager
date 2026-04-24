"""Metadata resolver — search Deezer, match results, fetch album data + covers.

Central logic shared by §3 (Identifier) and §6 (Import). Handles:
- ISRC lookup + album filter (best approach, tested on 70 real tracks: 100%)
- Title+artist search with multi-pass fuzzy matching (fallback)
- Album data fetch with best cover (iTunes 3000×3000 > Deezer 1000×1000)

All Deezer and iTunes API calls are encapsulated here.
"""

import threading
import time
import urllib.parse
from typing import Any

import requests

from music_manager.core.models import Track
from music_manager.core.normalize import first_artist, is_match, normalize, prepare_title
from music_manager.services.albums import Albums

# ── Constants ────────────────────────────────────────────────────────────────

_DEEZER_BASE = "https://api.deezer.com"
_ITUNES_BASE = "https://itunes.apple.com"
_HEADERS = {"Accept-Language": "en-US,en;q=0.9"}
_REQUEST_DELAY = 0.1
_REQUEST_TIMEOUT = 10
_ITUNES_COUNTRY = "US"  # set at startup via configure()
_SESSION = requests.Session()  # connection pooling (1.9x speedup on API calls)

_COMPILATION_ARTISTS = frozenset(
    {
        "various artists",
        "various",
        "compilations",
        "compilation",
        "multi-interpretes",
        "multi interpretes",
        "varios artistas",
        "artisti vari",
        "verschiedene interpreten",
        "original soundtrack",
        "original motion picture soundtrack",
        "bande originale",
        "ost",
    }
)

# Circuit breaker: skip requests after N consecutive failures
_CIRCUIT_BREAKER_THRESHOLD = 5
_CIRCUIT_BREAKER_COOLDOWN = 60  # seconds
_consecutive_failures = 0
_circuit_open_until = 0.0


def configure(language: str) -> None:
    """Set module-level settings from user config."""
    global _ITUNES_COUNTRY, _HEADERS  # noqa: PLW0603
    _ITUNES_COUNTRY = language.upper() if language else "US"
    lang = language.lower() if language else "en"
    _HEADERS = {"Accept-Language": f"{lang}-{lang.upper()},{lang};q=0.9"}


# ── Result type ──────────────────────────────────────────────────────────────


class ResolveResult:
    """Outcome of a resolve attempt."""

    def __init__(
        self,
        status: str,
        track: Track | None = None,
        candidates: list[dict] | None = None,
        album_mismatch: bool = False,
    ) -> None:
        self.status = status  # "resolved", "not_found", "ambiguous", "mismatch"
        self.track = track
        self.candidates = candidates or []
        self.album_mismatch = album_mismatch


# ── Entry point ──────────────────────────────────────────────────────────────


def resolve(
    title: str,
    artist: str,
    album: str,
    isrc: str,
    albums_store: Albums,
) -> ResolveResult:
    """Resolve a track via Deezer.

    Strategy (validated on 70 real tracks — 100% auto-resolution):
    1. ISRC → Deezer lookup → filter by album if provided
    2. If ISRC not on Deezer or no ISRC: fallback title+artist search
    """
    # 1. ISRC lookup + album filter
    if isrc:
        result = _resolve_by_isrc(isrc, title, artist, album, albums_store)
        if result:
            return result

    # 2. Fallback: title+artist search (multi-pass)
    return _search_and_match(title, artist, album, albums_store)


# ── Private Functions ────────────────────────────────────────────────────────


def _resolve_by_isrc(
    isrc: str,
    title: str,
    artist: str,
    album: str,
    albums_store: Albums,
) -> ResolveResult | None:
    """ISRC lookup + album filter.

    ISRC is reliable for the song. If the album differs from CSV, search
    Deezer for the same song on the right album. If not found, use the
    ISRC result anyway (correct song, different album).
    """
    data = deezer_get(f"/track/isrc:{isrc}")
    if not data or "error" in data:
        return None  # ISRC not on Deezer → caller falls back to search

    dz_title = data.get("title", "")
    dz_artist = data.get("artist", {}).get("name", "")
    dz_album_title = data.get("album", {}).get("title", "")

    # Sanity check: does the ISRC point to the right song?
    title_ok = _title_matches(title, dz_title)
    artist_ok = is_match(artist, dz_artist, "artist")
    isrc_is_correct = title_ok or artist_ok

    if not isrc_is_correct:
        # ISRC points to a completely different song → ignore, fallback to search
        return None

    # No album in CSV or album matches → resolved directly
    if not album or normalize(album) == normalize(dz_album_title):
        album_data = fetch_album_with_cover(data.get("album", {}).get("id", 0), albums_store)
        return ResolveResult("resolved", track=build_track(data, album_data))

    # Album differs → search for the same song on the correct album
    better = _search_better_album(title, artist, album, albums_store)
    if better and better.status == "resolved":
        return better

    # Correct album not found → mismatch (user decides)
    album_data = fetch_album_with_cover(data.get("album", {}).get("id", 0), albums_store)
    return ResolveResult("mismatch", track=build_track(data, album_data), album_mismatch=True)


def _search_better_album(
    title: str,
    artist: str,
    album: str,
    albums_store: Albums,
) -> ResolveResult | None:
    """Search for the same song on the correct album.

    Two strategies:
    1. Search by track title+artist → filter by album name
    2. Search by album name → find track in tracklist (like v1)
    """
    # Strategy 1: track search, filter by album, prefer exact title
    matches = _search_deezer(title, first_artist(artist))
    album_matches = [
        item
        for item in matches
        if normalize(album) == normalize(item.get("album", {}).get("title", ""))
    ]

    if album_matches:
        # Only use strategy 1 if there's an exact title match
        exact_title = [
            m for m in album_matches if normalize(title) == normalize(m.get("title", ""))
        ]
        if exact_title:
            album_data = fetch_album_with_cover(exact_title[0]["album"]["id"], albums_store)
            return ResolveResult("resolved", track=build_track(exact_title[0], album_data))
        # Soft matches only (Demo, Remix...) → prefer strategy 2 (tracklist)

    # Strategy 2: search album → find track in tracklist
    return _search_in_album(title, artist, album, albums_store)


def _search_in_album(
    title: str,
    artist: str,
    album: str,
    albums_store: Albums,
) -> ResolveResult | None:
    """Search for the album on Deezer, then find the track in its tracklist.

    If multiple editions match, returns ambiguous so the user can choose.
    """
    # Multi-query: full album+artist, then short album+artist
    import re  # noqa: PLC0415

    primary = first_artist(artist)
    clean_album = re.sub(r"[()[\]]", " ", album).strip()
    queries = [
        f"{clean_album} {primary}",
        f"{prepare_title(album)} {primary}",
    ]
    # Dedupe queries
    seen: set[str] = set()
    data_results: list[dict] = []
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        data = deezer_get(f"/search/album?q={urllib.parse.quote(q)}&limit=10")
        if data:
            data_results.extend(data.get("data", []))

    if not data_results:
        return None

    # Collect all matching albums (dedupe by ID)
    matching_albums = []
    seen_ids: set[int] = set()
    for alb in data_results:
        alb_title = alb.get("title", "")
        alb_artist = alb.get("artist", {}).get("name", "")

        # Album title must match (strict or soft)
        strict = normalize(album) == normalize(alb_title)
        soft = prepare_title(album) == prepare_title(alb_title)
        if not strict and not soft:
            continue
        # Dedupe
        alb_id = alb.get("id", 0)
        if alb_id in seen_ids:
            continue
        # Artist must match (skip for compilations like "Various Artists")
        is_compilation = normalize(alb_artist) in _COMPILATION_ARTISTS
        if not is_compilation and not is_match(first_artist(artist), alb_artist, "artist"):
            continue
        seen_ids.add(alb_id)
        matching_albums.append(alb)

    if not matching_albums:
        return None

    # Exact case-insensitive match first (preserves +, &, etc.)
    exact_ci = [a for a in matching_albums if album.lower() == a.get("title", "").lower()]
    if len(exact_ci) == 1:
        return _find_track_in_album(title, exact_ci[0], albums_store)

    # Fallback: normalize match (strips punctuation)
    exact = [a for a in matching_albums if normalize(album) == normalize(a.get("title", ""))]
    if len(exact) == 1:
        return _find_track_in_album(title, exact[0], albums_store)

    # Multiple editions → find the track in each, let user choose
    candidates = []
    for alb in matching_albums:
        track_result = _find_track_in_album_raw(title, alb, albums_store)
        if track_result:
            candidates.append(track_result)

    if len(candidates) == 1:
        full = deezer_get(f"/track/{candidates[0]['id']}")
        if full and "error" not in full:
            album_data = fetch_album_with_cover(candidates[0].get("album_id", 0), albums_store)
            return ResolveResult("resolved", track=build_track(full, album_data))

    if len(candidates) > 1:
        return ResolveResult("ambiguous", candidates=candidates)

    return None


def _best_track_match(title: str, tracks_list: list[dict]) -> dict | None:
    """Find best matching track: prefer normalize exact, fallback _title_matches."""
    # Pass 1: exact normalize match
    for item in tracks_list:
        if normalize(title) == normalize(item.get("title", "")):
            return item
    # Pass 2: soft match (_title_matches handles parens/dashes)
    for item in tracks_list:
        if _title_matches(title, item.get("title", "")):
            return item
    return None


def _find_track_in_album(title: str, alb: dict, albums_store: Albums) -> ResolveResult | None:
    """Find a track in an album's tracklist and return resolved."""
    tracks_data = get_album_tracklist(alb["id"], albums_store)
    if not tracks_data:
        return None

    match = _best_track_match(title, tracks_data)
    if match:
        full = deezer_get(f"/track/{match['id']}")
        if full and "error" not in full:
            album_data = fetch_album_with_cover(alb["id"], albums_store)
            return ResolveResult("resolved", track=build_track(full, album_data))
    return None


def _find_track_in_album_raw(
    title: str, alb: dict, albums_store: Albums | None = None
) -> dict | None:
    """Find a track in an album's tracklist and return raw dict for candidates."""
    tracks_data = get_album_tracklist(alb["id"], albums_store) if albums_store else None
    if not tracks_data:
        tracklist = deezer_get(f"/album/{alb['id']}/tracks?limit=50")
        tracks_data = tracklist.get("data", []) if tracklist else []
    if not tracks_data:
        return None

    match = _best_track_match(title, tracks_data)
    if not match:
        return None

    # Enrich with album info for display in review (copy to avoid mutating cache)
    match = {
        **match,
        "album": {
            "id": alb["id"],
            "title": alb.get("title", ""),
            "nb_tracks": alb.get("nb_tracks", 0),
        },
        "album_id": alb["id"],
    }
    return match


def _title_matches(csv_title: str, dz_title: str) -> bool:
    """Check if titles match: is_match OR same normalize (handles - vs () variants)."""
    if is_match(csv_title, dz_title, "title"):
        return True
    return normalize(csv_title) == normalize(dz_title)


def _search_deezer(title: str, artist: str) -> list[dict]:
    """Search Deezer and return matches filtered by title+artist."""
    if artist:
        query = f'track:"{title}" artist:"{artist}"'
    else:
        query = f'track:"{title}"'
    data = deezer_get(f"/search/track?q={urllib.parse.quote(query)}&limit=15")
    if not data:
        return []

    matches = []
    for item in data.get("data", []):
        dz_title = item.get("title", "")
        dz_artist = item.get("artist", {}).get("name", "")
        if _title_matches(title, dz_title) and (
            not artist or is_match(artist, dz_artist, "artist")
        ):
            matches.append(item)
    return matches


def _search_and_match(
    title: str,
    artist: str,
    album: str,
    albums_store: Albums,
) -> ResolveResult:
    """Fallback: search Deezer by title+artist, multi-pass.

    Pass 1: structured query track+artist (strict thresholds 85/90)
    Pass 2: first_artist only (strips feat/ft)
    Pass 3: free text query (catches localization)
    """
    # Pass 1: full artist
    matches = _search_deezer(title, artist)

    # Pass 2: first artist only
    if not matches:
        primary = first_artist(artist)
        if primary != artist:
            matches = _search_deezer(title, primary)

    # Pass 3: free text query, lower artist threshold (60%)
    if not matches:
        primary = first_artist(artist)
        query = f"{title} {primary}"
        data = deezer_get(f"/search/track?q={urllib.parse.quote(query)}&limit=15")
        if data:
            for item in data.get("data", []):
                dz_title = item.get("title", "")
                dz_artist = item.get("artist", {}).get("name", "")
                if is_match(title, dz_title, "title") and is_match(
                    primary, dz_artist, "artist", threshold=60.0
                ):
                    matches.append(item)

    if not matches:
        return ResolveResult("not_found")

    # Filter by album if provided — prefer exact case-insensitive over normalize
    if album:
        # Exact match (preserves +, &, etc.)
        album_matches = [
            item
            for item in matches
            if album.lower() == item.get("album", {}).get("title", "").lower()
        ]
        # Fallback: normalize match
        if not album_matches:
            album_matches = [
                item
                for item in matches
                if normalize(album) == normalize(item.get("album", {}).get("title", ""))
            ]

        if album_matches:
            # Prefer exact title match (avoids Demo/Remix when original exists)
            exact_title = [
                m for m in album_matches if normalize(title) == normalize(m.get("title", ""))
            ]
            if len(exact_title) == 1:
                full = _enrich_track_data(exact_title[0])
                album_data = fetch_album_with_cover(
                    full.get("album", {}).get("id", 0), albums_store
                )
                return ResolveResult("resolved", track=build_track(full, album_data))
            if len(exact_title) > 1:
                return ResolveResult("ambiguous", candidates=exact_title)

            # No exact title but album matches → try tracklist search first
            better = _search_in_album(title, artist, album, albums_store)
            if better:
                return better

            # Fallback to first album match (Demo/Remix)
            if len(album_matches) == 1:
                full = _enrich_track_data(album_matches[0])
                album_data = fetch_album_with_cover(
                    full.get("album", {}).get("id", 0), albums_store
                )
                return ResolveResult("resolved", track=build_track(full, album_data))
            return ResolveResult("ambiguous", candidates=album_matches)

        # 0 album match in track search → try album search
        better = _search_in_album(title, artist, album, albums_store)
        if better:
            return better

        # Still no match → mismatch or ambiguous
        if len(matches) == 1:
            full = _enrich_track_data(matches[0])
            album_data = fetch_album_with_cover(full.get("album", {}).get("id", 0), albums_store)
            return ResolveResult(
                "mismatch",
                track=build_track(full, album_data),
                album_mismatch=True,
            )
        return ResolveResult("ambiguous", candidates=matches)

    # No album provided
    if len(matches) == 1:
        full = _enrich_track_data(matches[0])
        album_data = fetch_album_with_cover(full.get("album", {}).get("id", 0), albums_store)
        return ResolveResult("resolved", track=build_track(full, album_data))
    return ResolveResult("ambiguous", candidates=matches)


# ── Album + Cover ──────────────────────────────────────────────────────────


def fetch_album_with_cover(album_id: int, albums_store: Albums) -> dict:
    """Fetch album data from Deezer + best cover from iTunes. Cache in albums_store."""
    if not album_id:
        return {}
    cached = albums_store.get(album_id)
    if cached:
        return cached

    album_data = deezer_get(f"/album/{album_id}")
    if not album_data or "error" in album_data:
        return {}

    genres = album_data.get("genres", {}).get("data", [])
    result = {
        "id": album_id,
        "title": album_data.get("title", ""),
        "artist": album_data.get("artist", {}).get("name", ""),
        "album_artist": album_data.get("artist", {}).get("name", ""),
        "genre": genres[0]["name"] if genres else "",
        "year": (album_data.get("release_date") or "")[:4],
        "release_date": album_data.get("release_date", ""),
        "total_tracks": album_data.get("nb_tracks", 0),
        "total_discs": album_data.get("nb_disk", 0),
        "cover_url": album_data.get("cover_xl", ""),
    }

    try:
        itunes_cover = _itunes_cover(
            result["title"],
            result["artist"],
            year=result["year"],
            total_tracks=result["total_tracks"],
        )
        if itunes_cover:
            result["cover_url"] = itunes_cover
    except Exception as exc:  # noqa: BLE001
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("cover_fetch_failed", album=result.get("title", ""), error=str(exc))

    albums_store.put(album_id, result)
    return result


def _itunes_cover(album_title: str, artist: str, year: str = "", total_tracks: int = 0) -> str:
    """Search iTunes for a high-res album cover. Returns URL or empty string.

    Uses year and track count to disambiguate albums with the same name.
    """
    try:
        response = _SESSION.get(
            f"{_ITUNES_BASE}/search",
            params={
                "term": f"{album_title} {artist}",
                "media": "music",
                "entity": "album",
                "limit": 5,
                "country": _ITUNES_COUNTRY,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        time.sleep(_REQUEST_DELAY)
        if response.status_code != 200:
            return ""
        results = response.json().get("results", [])
        name_matches = [
            item
            for item in results
            if normalize(item.get("collectionName", "")) == normalize(album_title)
        ]
        # Disambiguate: year+tracks > tracks alone > year alone > first
        best = None
        for item in name_matches:
            item_year = item.get("releaseDate", "")[:4]
            item_tracks = item.get("trackCount", 0)
            # Best: both year AND track count match
            if year and total_tracks and item_year == year and item_tracks == total_tracks:
                best = item
                break
        if not best:
            # Fallback: track count match (most reliable discriminator)
            for item in name_matches:
                if total_tracks and item.get("trackCount", 0) == total_tracks:
                    best = item
                    break
        if not best:
            # Fallback: year match
            for item in name_matches:
                if year and item.get("releaseDate", "")[:4] == year:
                    best = item
                    break
        if not best:
            if not name_matches:
                return ""
            best = name_matches[0]
        artwork = best.get("artworkUrl100", "")
        return artwork.replace("100x100bb", "3000x3000bb") if artwork else ""
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
        pass
    return ""


# ── Track builder ──────────────────────────────────────────────────────────


def get_album_tracklist(album_id: int, albums_store: Albums | None) -> list[dict]:
    """Get album tracklist, cached in albums_store."""
    if albums_store:
        album_data = albums_store.get(album_id)
        if album_data and "_tracklist" in album_data:
            return album_data["_tracklist"]

    tracklist = deezer_get(f"/album/{album_id}/tracks?limit=50")
    if not tracklist:
        return []

    tracks_data = tracklist.get("data", [])

    # Cache in albums_store
    if albums_store:
        album_data = albums_store.get(album_id)
        if album_data:
            album_data["_tracklist"] = tracks_data
            albums_store.put(album_id, album_data)

    return tracks_data


def _enrich_track_data(search_item: dict) -> dict:
    """Fetch full track data from /track/{id} if search result is incomplete."""
    if search_item.get("track_position") is not None:
        return search_item  # already complete
    track_id = search_item.get("id", 0)
    if not track_id:
        return search_item
    full = deezer_get(f"/track/{track_id}")
    return full if (full and "error" not in full) else search_item


def build_track(deezer_data: dict, album_data: dict) -> Track:
    """Build a Track from Deezer track response + album data."""
    return Track(
        isrc=(deezer_data.get("isrc") or "").upper(),
        title=deezer_data.get("title", ""),
        artist=deezer_data.get("artist", {}).get("name", ""),
        album=album_data.get("title", "") or deezer_data.get("album", {}).get("title", ""),
        album_id=deezer_data.get("album", {}).get("id", 0) or album_data.get("id", 0),
        genre=album_data.get("genre", ""),
        release_date=album_data.get("release_date", ""),
        track_number=deezer_data.get("track_position"),
        total_tracks=album_data.get("total_tracks"),
        disk_number=deezer_data.get("disk_number", 1),
        total_discs=album_data.get("total_discs", 0),
        album_artist=album_data.get("album_artist", ""),
        duration=deezer_data.get("duration", 0),
        explicit=deezer_data.get("explicit_lyrics", False),
        cover_url=album_data.get("cover_url", ""),
        preview_url=deezer_data.get("preview", ""),
        deezer_id=deezer_data.get("id", 0),
    )


# ── HTTP helpers ────────────────────────────────────────────────────────────


def http_get(url: str, timeout: int = _REQUEST_TIMEOUT, **kwargs: Any) -> requests.Response:
    """HTTP GET using the shared session (connection pooling).

    All modules should use this instead of raw ``requests.get()``.
    """
    return _SESSION.get(url, timeout=timeout, headers=_HEADERS, **kwargs)


def search_itunes_covers(album_title: str, artist: str) -> list[dict]:
    """Search iTunes for album covers matching album + artist.

    Returns list of dicts: {url, thumbnail, year, track_count, artist, album}.
    """
    from music_manager.core.normalize import is_match  # noqa: PLC0415

    if not album_title:
        return []

    norm_album = normalize(album_title)
    primary = first_artist(artist)

    try:
        response = http_get(
            f"{_ITUNES_BASE}/search",
            params={
                "term": f"{album_title} {primary}",
                "media": "music",
                "entity": "album",
                "limit": 15,
                "country": _ITUNES_COUNTRY,
            },
        )
        if response.status_code != 200:
            return []

        results: list[dict] = []
        seen_urls: set[str] = set()
        for item in response.json().get("results", []):
            norm_coll = normalize(item.get("collectionName", ""))
            if norm_album != norm_coll and not norm_coll.startswith(norm_album):
                continue
            itunes_artist = item.get("artistName", "")
            if not is_match(primary, itunes_artist, "artist"):
                continue
            artwork = item.get("artworkUrl100", "")
            if not artwork:
                continue
            url_3k = artwork.replace("100x100bb", "3000x3000bb")
            if url_3k in seen_urls:
                continue
            seen_urls.add(url_3k)
            results.append(
                {
                    "url": url_3k,
                    "thumbnail": artwork.replace("100x100bb", "300x300bb"),
                    "year": item.get("releaseDate", "")[:4],
                    "track_count": item.get("trackCount", 0),
                    "artist": itunes_artist,
                    "album": item.get("collectionName", ""),
                }
            )
        return results
    except Exception as exc:  # noqa: BLE001
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("search_itunes_covers_failed", error=str(exc))
        return []


# ── Public helpers (used by modify_track) ─────────────────────────────────


def resolve_by_id(deezer_id: int, albums_store: Albums) -> Track | None:
    """Resolve a track from its Deezer ID. Returns Track or None."""
    data = deezer_get(f"/track/{deezer_id}")
    if not data or "error" in data:
        return None
    album_id = data.get("album", {}).get("id", 0)
    album_data = fetch_album_with_cover(album_id, albums_store)
    return build_track(data, album_data)


def search_editions(title: str, artist: str) -> list[dict]:
    """Search Deezer for alternative editions of a track (different ISRCs).

    Searches with full title AND base title (stripped parens) to find
    all versions (studio, live, remastered, demo, etc.).

    Returns list of dicts: {deezer_id, title, artist, album, isrc,
    total_tracks, preview, album_id}.
    """
    primary = first_artist(artist)
    base_title = prepare_title(title)
    norm_title = normalize(title)

    # Search with both full title and base title
    queries = [f'{primary} "{title}"']
    prep = prepare_title(title)
    if prep != norm_title:
        # Base title differs (has parens stripped) → search both
        # Reconstruct readable base from prepare_title
        import re  # noqa: PLC0415

        stripped = re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*", " ", title).strip()
        if normalize(stripped) != norm_title:
            queries.append(f'{primary} "{stripped}"')

    results: list[dict] = []
    seen_isrc: set[str] = set()

    for query in queries:
        data = deezer_get(
            f"/search/track?q={urllib.parse.quote(query)}&limit=15",
        )
        if not data:
            continue

        for item in data.get("data", []):
            isrc = (item.get("isrc", "") or "").upper()
            if not isrc or isrc in seen_isrc:
                continue
            # Match: exact normalize OR same base title
            item_title = item.get("title", "")
            if normalize(item_title) != norm_title and prepare_title(item_title) != base_title:
                continue
            seen_isrc.add(isrc)
            alb = item.get("album", {})
            results.append(
                {
                    "deezer_id": item.get("id", 0),
                    "title": item_title,
                    "artist": item.get("artist", {}).get("name", ""),
                    "album": alb.get("title", ""),
                    "isrc": isrc,
                    "total_tracks": alb.get("nb_tracks", 0),
                    "preview": item.get("preview", ""),
                    "album_id": alb.get("id", 0),
                }
            )

    return results


def search_album_editions(
    album_title: str,
    artist: str,
    albums_store: Albums | None = None,
) -> list[dict]:
    """Search Deezer for alternative editions of an album.

    Returns list of dicts: {album_id, title, artist, nb_tracks, year}.
    """
    import re  # noqa: PLC0415

    primary = first_artist(artist)
    clean = re.sub(r"[()[\]]", " ", album_title).strip()
    queries = [f"{clean} {primary}", f"{prepare_title(album_title)} {primary}"]

    seen_ids: set[int] = set()
    results: list[dict] = []
    seen_q: set[str] = set()

    for q in queries:
        if q in seen_q:
            continue
        seen_q.add(q)
        data = deezer_get(f"/search/album?q={urllib.parse.quote(q)}&limit=10")
        if not data:
            continue
        for alb in data.get("data", []):
            alb_id = alb.get("id", 0)
            if alb_id in seen_ids:
                continue
            alb_title = alb.get("title", "")
            alb_artist = alb.get("artist", {}).get("name", "")
            # Album title must match
            if not (
                normalize(album_title) == normalize(alb_title)
                or prepare_title(album_title) == prepare_title(alb_title)
            ):
                continue
            # Artist must match
            is_compilation = normalize(alb_artist) in _COMPILATION_ARTISTS
            if not is_compilation and not is_match(primary, alb_artist, "artist"):
                continue
            seen_ids.add(alb_id)

            # Get year from cache or API
            year = ""
            if albums_store:
                cached = albums_store.get(alb_id)
                if cached:
                    year = cached.get("year", "")
            if not year:
                alb_data = deezer_get(f"/album/{alb_id}")
                if alb_data:
                    year = (alb_data.get("release_date") or "")[:4]

            results.append(
                {
                    "album_id": alb_id,
                    "title": alb_title,
                    "artist": alb_artist,
                    "nb_tracks": alb.get("nb_tracks", 0),
                    "year": year,
                }
            )

    return results


def download_cover_file(cover_url: str, tmp_dir: str, name: str = "modify") -> str:
    """Download a cover image to tmp_dir. Returns file path or empty string."""
    if not cover_url:
        return ""
    try:
        import os  # noqa: PLC0415

        response = _SESSION.get(cover_url, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        ext = ".png" if "png" in response.headers.get("content-type", "") else ".jpg"
        path = os.path.join(tmp_dir, f"{name}{ext}")
        os.makedirs(tmp_dir, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(response.content)
        return path
    except (requests.ConnectionError, requests.Timeout, OSError):
        return ""


# ── HTTP ───────────────────────────────────────────────────────────────────


_API_CACHE: dict[str, dict | None] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX_SIZE = 2000


def clear_api_cache() -> None:
    """Clear in-memory API cache. Call at start of major operations."""
    with _CACHE_LOCK:
        _API_CACHE.clear()


def deezer_get(endpoint: str) -> dict | None:
    """GET a Deezer API endpoint. Returns parsed JSON or None.

    Uses in-memory cache (LRU-bounded) to avoid redundant calls.
    Thread-safe via _CACHE_LOCK. Circuit breaker after consecutive failures.
    """
    global _consecutive_failures, _circuit_open_until  # noqa: PLW0603

    with _CACHE_LOCK:
        if endpoint in _API_CACHE:
            return _API_CACHE[endpoint]

    # Circuit breaker: skip if too many recent failures
    if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
        if time.time() < _circuit_open_until:
            return None
        # Cooldown expired — try again
        _consecutive_failures = 0

    try:
        response = _SESSION.get(
            f"{_DEEZER_BASE}{endpoint}",
            headers=_HEADERS,
            timeout=_REQUEST_TIMEOUT,
        )
        time.sleep(_REQUEST_DELAY)
        if response.status_code != 200:
            _consecutive_failures += 1
            _circuit_open_until = time.time() + _CIRCUIT_BREAKER_COOLDOWN
            return None
        data = response.json()
        if "error" in data:
            return None
        # Success — reset circuit breaker
        _consecutive_failures = 0
        with _CACHE_LOCK:
            if len(_API_CACHE) >= _CACHE_MAX_SIZE:
                oldest = next(iter(_API_CACHE))
                del _API_CACHE[oldest]
            _API_CACHE[endpoint] = data
        return data
    except (requests.ConnectionError, requests.Timeout):
        _consecutive_failures += 1
        _circuit_open_until = time.time() + _CIRCUIT_BREAKER_COOLDOWN
        return None
