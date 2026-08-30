"""Recommendation feed — Spotify Home style sections for the dashboard widget.

Runs on-demand (via the ``recos-feed`` CLI) and returns a JSON payload
consumed by ``dashboard.jsx``. The widget renders each section as a
horizontal-scrollable row of track cards; the user cherry-picks with
the existing preview + stack UX, no auto-import.

Sections:

- ``recent``          — Based on your latest imports (per-seed sub-cards).
- ``similar_artist``  — Similar to your top artists (per-artist sub-cards).
- ``new_releases``    — Recent releases by artists you follow.
- ``artist_radio``    — Algorithmic radio for your top artists.
- ``genre_trends``    — Editorial charts for your top genres.
- ``explorations``    — Discovery mode (cold artists, novelty bias).

Sources blended:

- Deezer (``deezer_reco``): related / radio / editorial / new-releases.
- Spotify (``services.spotify``): related-artists + top-tracks (auth
  optional — falls back gracefully).
- Last.fm (``services.lastfm``): ``track.getSimilar`` for the ``recent``
  section (compensates when a track has no Deezer equivalent). Requires
  an API key already stored — silently skipped otherwise.

Cache:

- ``${data_root}/reco_feed_cache.json`` — TTL 30 min.
- Invalidated when ``signals.jsonl`` was written after the last feed
  generation (fresh outcomes must shift the affinity scores).
"""

import json
import os
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from music_manager.core.config import Paths
from music_manager.core.logger import log_event
from music_manager.core.models import Track
from music_manager.core.profile import Profile, build_profile
from music_manager.pipeline.reco_scoring import (
    RecommendationCandidate,
    blend_candidates,
    candidate_to_widget_track,
    rerank_for_section,
    slugify,
)
from music_manager.services import deezer_reco, lastfm, spotify
from music_manager.services.albums import Albums
from music_manager.services.recommendations_store import RecommendationsStore
from music_manager.services.resolver import (
    build_track,
    deezer_get,
    fetch_album_preview,
    search_track,
)
from music_manager.services.signals import SignalsLog
from music_manager.services.tracks import Tracks

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_SECTIONS: tuple[str, ...] = (
    "recent",
    "similar_artist",
    "new_releases",
    "artist_radio",
    "genre_trends",
    "explorations",
)
_DEFAULT_MAX_PER_SECTION = 10
_CACHE_TTL_SECONDS = 30 * 60

# How many seeds we hit per section — kept small because each seed spawns
# 20-40 API calls (Deezer + Spotify + Last.fm blend + resolve).
_RECENT_SEEDS = 3
_SIMILAR_ARTIST_SEEDS = 3
_NEW_RELEASE_ARTISTS = 6
_ARTIST_RADIO_SEEDS = 3
_GENRE_TRENDS_SEEDS = 3

_EXPLORATION_MATCH_MIN = 0.4
_EXPLORATION_MATCH_MAX = 0.7

_TRACK_RADIO_LIMIT = 25
_ARTIST_RADIO_LIMIT = 25

# Parallelism — Deezer calls are HTTP-bound and paced by the resolver's
# global rate limiter, so extra workers buy latency, not throughput.
_RESOLVE_WORKERS = 8

# Every raw radio/chart item costs a /track/{id} call to get its ISRC, and
# ranking then keeps only a handful. Enriching all 25 items of a radio to
# display 3 is what pushed one feed build to ~450 Deezer calls. The pools
# arrive ordered by relevance, so enriching a bounded head is enough to
# still fill a section after dedup drops library tracks and duplicates.
_MAX_ENRICH_PER_POOL = 14

# In-process guard so two widget refreshes (Übersicht auto-refresh + user
# clicking "Actualiser") don't race on the same expensive computation.
_BUILD_LOCK = threading.Lock()


# ── Public entry points ──────────────────────────────────────────────────────


def build_feed(
    paths: Paths,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog | None = None,
    *,
    sections: Iterable[str] = DEFAULT_SECTIONS,
    max_per_section: int = _DEFAULT_MAX_PER_SECTION,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return the full recommendation feed as a widget-ready JSON dict.

    ``sections`` is any subset of :data:`DEFAULT_SECTIONS`. Order matters
    (that's the order the widget renders them). The cache is bypassed
    when ``force_refresh=True`` or when ``signals.jsonl`` was modified
    after the cached payload was generated.
    """
    with _BUILD_LOCK:
        wanted = [s for s in sections if s in DEFAULT_SECTIONS]
        cache_path = _cache_path(paths)
        if not force_refresh:
            cached = _load_cache(cache_path, paths.signals_log_path, wanted)
            if cached is not None:
                cached["cache_hit"] = True
                return cached

        sig = signals if signals is not None else SignalsLog(paths.signals_log_path)
        profile = build_profile(tracks_store.all(), mode="library")

        # Sections are independent — run them concurrently to shave off
        # cold-start latency. Each internally uses its own thread pool
        # for per-item Deezer calls, so total concurrency stays bounded.
        payload_sections: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        results: dict[str, Any] = {}

        def build(name: str) -> tuple[str, dict[str, Any] | None, str]:
            try:
                out = _build_one_section(
                    name,
                    profile=profile,
                    tracks_store=tracks_store,
                    albums_store=albums_store,
                    recs_store=recs_store,
                    signals=sig,
                    max_per_section=max_per_section,
                )
                return name, out, ""
            except Exception as exc:  # noqa: BLE001
                log_event("reco_feed_section_failed", section=name, error=str(exc))
                return name, None, str(exc)[:200]

        if wanted:
            with ThreadPoolExecutor(max_workers=len(wanted)) as pool_exec:
                for name, section, error in pool_exec.map(build, wanted):
                    results[name] = section
                    if error:
                        errors[name] = error

        # Preserve the caller's requested order — dicts don't guarantee it
        # even though CPython 3.7+ inserts sequentially.
        for name in wanted:
            section = results.get(name)
            if section and (section.get("tracks") or section.get("subcards")):
                payload_sections.append(section)

        payload = {
            "generated_at": _now_iso(),
            "sections": payload_sections,
            "errors": errors,
            "cache_hit": False,
        }
        _write_cache(cache_path, payload, paths.signals_log_path)
        return payload


def build_track_radio(
    seed_isrc: str,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog | None = None,
    *,
    deezer_id: int = 0,
    seed_title: str = "",
    seed_artist: str = "",
    cover_url: str = "",
) -> dict[str, Any]:
    """Return a widget-ready "Radio de ce titre" preview payload.

    Two seeding paths:

    - ``seed_isrc`` already in the library — we look up ``tracks_store``
      to grab title/artist/deezer_id/cover.
    - Explicit ``deezer_id`` + optional title/artist/cover — used by the
      widget when the user clicks the radio icon on a Deezer search
      result that isn't imported yet.

    At least one of the two must produce a Deezer id; otherwise we
    return an empty preview.
    """
    isrc = (seed_isrc or "").upper()
    if isrc:
        entry = tracks_store.get_by_isrc(isrc)
        if entry:
            seed_title = seed_title or str(entry.get("title") or "")
            seed_artist = seed_artist or str(entry.get("artist") or "")
            deezer_id = deezer_id or int(entry.get("deezer_id") or 0)
            cover_url = cover_url or str(entry.get("cover_url") or "")
    if not deezer_id:
        return _empty_preview(f"Radio de {seed_title}" if seed_title else "Radio")

    profile = build_profile(tracks_store.all(), mode="library")
    pool = _collect_track_radio_candidates(
        isrc,
        seed_title=seed_title,
        seed_artist=seed_artist,
        deezer_id=deezer_id,
        albums_store=albums_store,
    )
    ranked = rerank_for_section(
        pool,
        profile,
        tracks_store,
        recs_store,
        signals=signals,
        mode="library",
        max_per_artist=2,
        limit=_TRACK_RADIO_LIMIT,
    )
    return {
        "name": f"Radio de {seed_title}" if seed_title else "Radio",
        "creator": seed_artist,
        "nb_tracks": len(ranked),
        "cover_url": cover_url,
        "cover_thumb": cover_url,
        "tracks": [candidate_to_widget_track(c, tracks_store=tracks_store) for c in ranked],
        "skipped": 0,
    }


def build_playlist_radio(
    playlist_name: str,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog | None = None,
    *,
    persistent_id: str = "",
) -> dict[str, Any]:
    """Return "Radio de cette playlist" — expand seeds from the playlist itself.

    Takes the top 3 tracks of the Apple Music playlist (by local play_count),
    fans out via Deezer track-radio, blends the pools, reranks. Same output
    shape as ``build_track_radio`` so the widget consumes it identically.
    """
    from music_manager.services import apple  # noqa: PLC0415

    name = (playlist_name or "").strip()
    if not name:
        return _empty_preview("Radio")
    try:
        apple_ids = apple.get_playlist_tracks(name) or []
    except Exception:  # noqa: BLE001
        apple_ids = []
    if not apple_ids:
        return _empty_preview(f"Radio {name}")

    seed_entries: list[dict[str, Any]] = []
    for apple_id in apple_ids:
        entry = tracks_store.get_by_apple_id(apple_id)
        if entry and int(entry.get("deezer_id") or 0):
            seed_entries.append(entry)
    seed_entries.sort(key=lambda e: int(e.get("play_count") or 0), reverse=True)
    top_seeds = seed_entries[:3]
    if not top_seeds:
        return _empty_preview(f"Radio {name}")

    def gather(entry: dict[str, Any]) -> list[dict[str, Any]]:
        deezer_id = int(entry.get("deezer_id") or 0)
        return deezer_reco.track_radio(deezer_id) if deezer_id else []

    raw_items: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, len(top_seeds))) as pool_exec:
        for chunk in pool_exec.map(gather, top_seeds):
            raw_items.extend(chunk)

    pool = _deezer_items_to_candidates(
        raw_items,
        source="deezer_track_radio",
        seed_isrc="",
        albums_store=albums_store,
    )
    profile = build_profile(tracks_store.all(), mode="library")
    ranked = rerank_for_section(
        pool,
        profile,
        tracks_store,
        recs_store,
        signals=signals,
        mode="library",
        max_per_artist=1,
        limit=25,
    )
    cover_url = _sample_cover_from_entries(seed_entries)
    return {
        "name": f"Radio {name}",
        "creator": "",
        "nb_tracks": len(ranked),
        "cover_url": cover_url,
        "cover_thumb": cover_url,
        "tracks": [candidate_to_widget_track(c, tracks_store=tracks_store) for c in ranked],
        "skipped": 0,
    }


def build_artist_radio(
    artist_name: str,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog | None = None,
) -> dict[str, Any]:
    """Return a widget-ready "Radio <artist>" preview payload."""
    clean = (artist_name or "").strip()
    if not clean:
        return _empty_preview("Radio")

    deezer_id = deezer_reco.resolve_artist_id(clean)
    pool = _collect_artist_radio_candidates(
        clean, deezer_id=deezer_id, albums_store=albums_store
    )
    profile = build_profile(tracks_store.all(), mode="library")
    ranked = rerank_for_section(
        pool,
        profile,
        tracks_store,
        recs_store,
        signals=signals,
        mode="library",
        max_per_artist=3,
        limit=_ARTIST_RADIO_LIMIT,
    )
    return {
        "name": f"Radio {clean}",
        "creator": clean,
        "nb_tracks": len(ranked),
        "cover_url": "",
        "cover_thumb": "",
        "tracks": [candidate_to_widget_track(c, tracks_store=tracks_store) for c in ranked],
        "skipped": 0,
    }


# ── Section builders ─────────────────────────────────────────────────────────


def _build_one_section(
    name: str,
    *,
    profile: Profile,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog,
    max_per_section: int,
) -> dict[str, Any] | None:
    if name == "recent":
        return _section_recent(
            profile=profile,
            tracks_store=tracks_store,
            albums_store=albums_store,
            recs_store=recs_store,
            signals=signals,
            limit=max_per_section,
        )
    if name == "similar_artist":
        return _section_similar_artist(
            profile=profile,
            tracks_store=tracks_store,
            albums_store=albums_store,
            recs_store=recs_store,
            signals=signals,
            limit=max_per_section,
        )
    if name == "new_releases":
        return _section_new_releases(
            profile=profile,
            tracks_store=tracks_store,
            albums_store=albums_store,
            recs_store=recs_store,
            signals=signals,
            limit=max_per_section,
        )
    if name == "artist_radio":
        return _section_artist_radio(
            profile=profile,
            tracks_store=tracks_store,
            albums_store=albums_store,
            recs_store=recs_store,
            signals=signals,
            limit=max_per_section,
        )
    if name == "genre_trends":
        return _section_genre_trends(
            profile=profile,
            tracks_store=tracks_store,
            albums_store=albums_store,
            recs_store=recs_store,
            signals=signals,
            limit=max_per_section,
        )
    if name == "explorations":
        return _section_explorations(
            profile=profile,
            tracks_store=tracks_store,
            albums_store=albums_store,
            recs_store=recs_store,
            signals=signals,
            limit=max_per_section,
        )
    return None


def _section_recent(
    *,
    profile: Profile,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog,
    limit: int,
) -> dict[str, Any]:
    """One sub-card per recent import — 'Because you liked X'.

    Each seed spawns Deezer + Last.fm queries; running them in parallel
    means the section completes in the time of its slowest seed rather
    than the sum of all of them.
    """
    seeds = _recent_import_seeds(tracks_store, limit=_RECENT_SEEDS)

    def gather(seed: dict[str, Any]) -> tuple[dict[str, Any], list[RecommendationCandidate]]:
        pool = _collect_track_radio_candidates(
            seed["isrc"],
            seed_title=seed["title"],
            seed_artist=seed["artist"],
            deezer_id=seed["deezer_id"],
            albums_store=albums_store,
        )
        return seed, pool

    per_seed: list[tuple[dict[str, Any], list[RecommendationCandidate]]] = []
    with ThreadPoolExecutor(max_workers=max(1, len(seeds))) as pool_exec:
        for outcome in pool_exec.map(gather, seeds):
            per_seed.append(outcome)

    subcards: list[dict[str, Any]] = []
    for seed, pool in per_seed:
        ranked = rerank_for_section(
            pool,
            profile,
            tracks_store,
            recs_store,
            signals=signals,
            mode="library",
            max_per_artist=1,
            limit=limit,
        )
        if not ranked:
            continue
        subcards.append(
            {
                "id": f"recent-{slugify(seed['title'])[:20]}-{seed['isrc'][:6]}",
                "title": seed["title"],
                "subtitle": seed["artist"],
                "seed": {
                    "kind": "track",
                    "isrc": seed["isrc"],
                    "title": seed["title"],
                    "artist": seed["artist"],
                    "cover_url": seed["cover_url"],
                },
                "tracks": [
                    candidate_to_widget_track(c, tracks_store=tracks_store) for c in ranked
                ],
            }
        )
    return {
        "id": "recent",
        "title": "Basé sur tes écoutes récentes",
        "subtitle": "Radios inspirées de tes derniers imports",
        "layout": "subcards",
        "subcards": subcards,
    }


def _section_similar_artist(
    *,
    profile: Profile,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog,
    limit: int,
) -> dict[str, Any]:
    """One sub-card per top artist — pooled related-artists tracks.

    Fans out one thread per seed artist so 3 sub-cards render in the
    time of the slowest one.
    """
    top = profile.top_artists[:_SIMILAR_ARTIST_SEEDS]

    def gather(item: tuple[str, int]) -> tuple[str, list[RecommendationCandidate]]:
        artist_name, _score = item
        return artist_name, _collect_similar_artist_candidates(
            artist_name, albums_store=albums_store
        )

    per_artist: list[tuple[str, list[RecommendationCandidate]]] = []
    if top:
        with ThreadPoolExecutor(max_workers=len(top)) as pool_exec:
            per_artist = list(pool_exec.map(gather, top))

    subcards: list[dict[str, Any]] = []
    for artist_name, pool in per_artist:
        ranked = rerank_for_section(
            pool,
            profile,
            tracks_store,
            recs_store,
            signals=signals,
            mode="library",
            max_per_artist=1,
            limit=limit,
        )
        if not ranked:
            continue
        subcards.append(
            {
                "id": f"similar-{slugify(artist_name)[:24]}",
                "title": f"Similaire à {artist_name}",
                "subtitle": "",
                "seed": {"kind": "artist", "name": artist_name},
                "tracks": [
                    candidate_to_widget_track(c, tracks_store=tracks_store) for c in ranked
                ],
            }
        )
    return {
        "id": "similar_artist",
        "title": "Similaire à tes artistes",
        "subtitle": "Radios d'artistes proches de ceux que tu écoutes",
        "layout": "subcards",
        "subcards": subcards,
    }


def _section_new_releases(
    *,
    profile: Profile,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog,
    limit: int,
) -> dict[str, Any]:
    """Flat list of recent releases by tracked artists.

    Parallelised across artists — Deezer lookups (``resolve_artist_id`` +
    ``artist_recent_albums`` + ``album_tracks``) are network-bound so a
    thread pool over the top-N artists brings this from ~30 s down to
    a handful of seconds.
    """

    def gather(artist_name: str) -> list[dict[str, Any]]:
        artist_id = deezer_reco.resolve_artist_id(artist_name)
        if not artist_id:
            return []
        gathered: list[dict[str, Any]] = []
        albums = deezer_reco.artist_recent_albums(artist_id, max_age_days=180, limit=4)
        for album in albums:
            album_id = int(album.get("id") or 0)
            if not album_id:
                continue
            tracks_raw = deezer_reco.album_tracks(album_id)
            # Cap 1 track per album so a fresh 20-track LP doesn't drown
            # everything else in the section.
            for item in tracks_raw[:1]:
                item["_album_id"] = album_id
                gathered.append(item)
        return gathered

    raw_items: list[dict[str, Any]] = []
    seen_albums: set[int] = set()
    with ThreadPoolExecutor(max_workers=_RESOLVE_WORKERS) as pool:
        futures = [
            pool.submit(gather, name)
            for name, _ in profile.top_artists[:_NEW_RELEASE_ARTISTS]
        ]
        for future in futures:
            for item in future.result():
                album_id = int(item.pop("_album_id", 0))
                if album_id and album_id in seen_albums:
                    continue
                if album_id:
                    seen_albums.add(album_id)
                raw_items.append(item)

    all_candidates = _deezer_items_to_candidates(
        raw_items,
        source="deezer_new_release",
        seed_isrc="",
        albums_store=albums_store,
    )
    ranked = rerank_for_section(
        all_candidates,
        profile,
        tracks_store,
        recs_store,
        signals=signals,
        mode="library",
        max_per_artist=2,
        limit=limit * 2,
    )
    return {
        "id": "new_releases",
        "title": "Nouveautés de tes artistes",
        "subtitle": "Sorties des 6 derniers mois",
        "layout": "row",
        "tracks": [candidate_to_widget_track(c, tracks_store=tracks_store) for c in ranked],
    }


def _section_artist_radio(
    *,
    profile: Profile,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog,
    limit: int,
) -> dict[str, Any]:
    subcards: list[dict[str, Any]] = []
    for artist_name, _score in profile.top_artists[:_ARTIST_RADIO_SEEDS]:
        artist_id = deezer_reco.resolve_artist_id(artist_name)
        if not artist_id:
            continue
        pool = _deezer_items_to_candidates(
            deezer_reco.artist_radio(artist_id),
            source="deezer_artist_radio",
            seed_isrc="",
            albums_store=albums_store,
        )
        ranked = rerank_for_section(
            pool,
            profile,
            tracks_store,
            recs_store,
            signals=signals,
            mode="library",
            max_per_artist=3,
            limit=limit,
        )
        if not ranked:
            continue
        subcards.append(
            {
                "id": f"radio-{slugify(artist_name)[:24]}",
                "title": f"Radio {artist_name}",
                "subtitle": "",
                "seed": {"kind": "artist", "name": artist_name},
                "tracks": [
                    candidate_to_widget_track(c, tracks_store=tracks_store) for c in ranked
                ],
            }
        )
    return {
        "id": "artist_radio",
        "title": "Radio de tes artistes",
        "subtitle": "Stations générées autour de tes préférés",
        "layout": "subcards",
        "subcards": subcards,
    }


def _section_genre_trends(
    *,
    profile: Profile,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog,
    limit: int,
) -> dict[str, Any]:
    subcards: list[dict[str, Any]] = []
    for genre_name, _count in profile.top_genres[:_GENRE_TRENDS_SEEDS]:
        genre_id = deezer_reco.resolve_genre_id(genre_name)
        raw = deezer_reco.genre_charts(genre_id if genre_id else 0, limit=30)
        pool = _deezer_items_to_candidates(
            raw,
            source="deezer_genre_chart",
            seed_isrc="",
            albums_store=albums_store,
        )
        ranked = rerank_for_section(
            pool,
            profile,
            tracks_store,
            recs_store,
            signals=signals,
            mode="library",
            max_per_artist=1,
            limit=limit,
        )
        if not ranked:
            continue
        subcards.append(
            {
                "id": f"genre-{slugify(genre_name)[:24]}",
                "title": f"Tendances {genre_name}",
                "subtitle": "",
                "seed": {"kind": "genre", "name": genre_name},
                "tracks": [
                    candidate_to_widget_track(c, tracks_store=tracks_store) for c in ranked
                ],
            }
        )
    return {
        "id": "genre_trends",
        "title": "Tendances par genre",
        "subtitle": "Top éditorial Deezer sur tes genres favoris",
        "layout": "subcards",
        "subcards": subcards,
    }


def _section_explorations(
    *,
    profile: Profile,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog,
    limit: int,
) -> dict[str, Any]:
    """Discovery mode without auto-import — cold artists surfaced for review."""
    pool: list[RecommendationCandidate] = []
    if lastfm.get_api_key():
        for seed_isrc, seed_title, seed_artist in profile.top_tracks[:8]:
            for item in lastfm.get_similar_tracks(
                seed_artist, seed_title, limit=_MAX_ENRICH_PER_POOL
            ):
                match = float(item.get("match") or 0.0)
                if match < _EXPLORATION_MATCH_MIN or match > _EXPLORATION_MATCH_MAX:
                    continue
                name = str(item.get("name") or "").strip()
                artist = str(item.get("artist") or "").strip()
                if not name or not artist:
                    continue
                # Resolve on Deezer to get an ISRC + cover.
                cand = _lastfm_to_candidate(
                    name,
                    artist,
                    match,
                    source="lastfm_similar",
                    seed_isrc=seed_isrc,
                    albums_store=albums_store,
                )
                if cand:
                    pool.append(cand)
    ranked = rerank_for_section(
        pool,
        profile,
        tracks_store,
        recs_store,
        signals=signals,
        mode="discovery",
        max_per_artist=1,
        limit=limit,
    )
    return {
        "id": "explorations",
        "title": "Explorations",
        "subtitle": "Artistes que tu n'écoutes pas encore",
        "layout": "row",
        "tracks": [candidate_to_widget_track(c, tracks_store=tracks_store) for c in ranked],
    }


# ── Candidate pools (multi-source blending) ──────────────────────────────────


def _collect_track_radio_candidates(
    seed_isrc: str,
    *,
    seed_title: str,
    seed_artist: str,
    deezer_id: int,
    albums_store: Albums,
) -> list[RecommendationCandidate]:
    """Blend Deezer track-radio + Last.fm similar (when available)."""
    deezer_pool: list[RecommendationCandidate] = []
    if deezer_id:
        deezer_pool = _deezer_items_to_candidates(
            deezer_reco.track_radio(deezer_id),
            source="deezer_track_radio",
            seed_isrc=seed_isrc,
            albums_store=albums_store,
        )

    lastfm_pool: list[RecommendationCandidate] = []
    if seed_title and seed_artist and lastfm.get_api_key():
        raw = lastfm.get_similar_tracks(seed_artist, seed_title, limit=_MAX_ENRICH_PER_POOL)
        with ThreadPoolExecutor(max_workers=_RESOLVE_WORKERS) as pool:
            futures = [
                pool.submit(
                    _lastfm_to_candidate,
                    str(item.get("name") or ""),
                    str(item.get("artist") or ""),
                    float(item.get("match") or 0.0),
                    source="lastfm_similar",
                    seed_isrc=seed_isrc,
                    albums_store=albums_store,
                )
                for item in raw
            ]
            for future in futures:
                cand = future.result()
                if cand:
                    lastfm_pool.append(cand)

    return blend_candidates([deezer_pool, lastfm_pool])


def _collect_similar_artist_candidates(
    artist_name: str,
    *,
    albums_store: Albums,
) -> list[RecommendationCandidate]:
    """Blend Deezer related-artists top-tracks + Spotify related-artists top-tracks.

    Parallelised per related artist — each related artist triggers a
    ``/artist/{id}/top`` call, so 8 related artists = 8 network calls we
    can execute concurrently.
    """
    deezer_raw: list[dict[str, Any]] = []
    deezer_artist_id = deezer_reco.resolve_artist_id(artist_name)
    if deezer_artist_id:
        related = deezer_reco.artist_related(deezer_artist_id, limit=8)
        with ThreadPoolExecutor(max_workers=_RESOLVE_WORKERS) as pool:
            futures = [
                pool.submit(deezer_reco.artist_top_tracks, int(rel.get("id") or 0), limit=3)
                for rel in related
                if int(rel.get("id") or 0)
            ]
            for future in futures:
                deezer_raw.extend(future.result())
    deezer_pool = _deezer_items_to_candidates(
        deezer_raw,
        source="deezer_related_top",
        seed_isrc="",
        albums_store=albums_store,
    )

    spotify_pool: list[RecommendationCandidate] = []
    if spotify.is_authenticated():
        sp_artist_id = spotify.resolve_artist_id(artist_name)
        if sp_artist_id:
            related = spotify.fetch_related_artists(sp_artist_id)[:8]
            with ThreadPoolExecutor(max_workers=_RESOLVE_WORKERS) as pool:
                futures = [
                    pool.submit(spotify.fetch_artist_top_tracks, str(rel.get("spotify_id") or ""))
                    for rel in related
                    if rel.get("spotify_id")
                ]
                for future in futures:
                    for item in future.result()[:3]:
                        cand = _spotify_track_to_candidate(
                            item,
                            source="spotify_related_top",
                            seed_isrc="",
                            base_score=70.0,
                        )
                        if cand:
                            spotify_pool.append(cand)

    return blend_candidates([deezer_pool, spotify_pool])


def _collect_artist_radio_candidates(
    artist_name: str,
    *,
    deezer_id: int,
    albums_store: Albums,
) -> list[RecommendationCandidate]:
    """Blend Deezer artist-radio + Spotify artist top-tracks."""
    deezer_pool: list[RecommendationCandidate] = []
    if deezer_id:
        deezer_pool = _deezer_items_to_candidates(
            deezer_reco.artist_radio(deezer_id),
            source="deezer_artist_radio",
            seed_isrc="",
            albums_store=albums_store,
        )

    spotify_pool: list[RecommendationCandidate] = []
    if spotify.is_authenticated():
        sp_artist_id = spotify.resolve_artist_id(artist_name)
        if sp_artist_id:
            for item in spotify.fetch_artist_top_tracks(sp_artist_id):
                cand = _spotify_track_to_candidate(
                    item, source="spotify_artist_top", seed_isrc="", base_score=75.0
                )
                if cand:
                    spotify_pool.append(cand)

    return blend_candidates([deezer_pool, spotify_pool])


# ── Item → Candidate adapters ────────────────────────────────────────────────


def _deezer_items_to_candidates(
    items: list[dict[str, Any]],
    *,
    source: str,
    seed_isrc: str,
    albums_store: Albums,
) -> list[RecommendationCandidate]:
    """Parallel version of ``_deezer_item_to_candidate`` for a raw list.

    Each item may trigger a ``/track/{id}`` enrichment call — running
    them in a thread pool cuts a 40-track radio from ~8 s to ~1 s.
    """
    if not items:
        return []
    resolved: list[RecommendationCandidate] = []
    with ThreadPoolExecutor(max_workers=_RESOLVE_WORKERS) as pool:
        futures = [
            pool.submit(
                _deezer_item_to_candidate,
                item,
                source=source,
                seed_isrc=seed_isrc,
                albums_store=albums_store,
            )
            for item in items[:_MAX_ENRICH_PER_POOL]
        ]
        for future in futures:
            cand = future.result()
            if cand is not None:
                resolved.append(cand)
    return resolved


def _deezer_item_to_candidate(
    item: dict[str, Any],
    *,
    source: str,
    seed_isrc: str,
    albums_store: Albums,
) -> RecommendationCandidate | None:
    """Convert a raw Deezer track item into a fully-populated candidate.

    ``item`` typically comes from a radio / chart / album-tracks endpoint
    and may lack ISRC — we call ``/track/{id}`` to enrich, then reuse the
    resolver's ``build_track`` + album cover fetch so the widget gets the
    same quality of metadata as a normal search result.
    """
    if not isinstance(item, dict):
        return None
    track_id = int(item.get("id") or 0)
    if not track_id:
        return None
    full = item
    if not item.get("isrc"):
        fetched = deezer_get(f"/track/{track_id}")
        if fetched and "error" not in fetched:
            full = fetched
    isrc = str(full.get("isrc") or "").upper()
    if not isrc:
        return None
    album_id = int((full.get("album") or {}).get("id") or 0)
    try:
        album_data = fetch_album_preview(album_id, albums_store) if album_id else {}
    except Exception:  # noqa: BLE001
        album_data = {}
    track = build_track(full, album_data)
    return RecommendationCandidate(
        isrc=isrc,
        deezer_id=track.deezer_id or track_id,
        title=track.title,
        artist=track.artist,
        track=track,
        source=source,
        seed_isrc=seed_isrc,
        # Deezer endpoints don't expose a similarity score, so we start
        # from a neutral baseline; scoring bonuses (recency, playcount,
        # affinity) drive the ranking.
        score=60.0,
        sources={source},
    )


def _spotify_track_to_candidate(
    item: dict[str, Any],
    *,
    source: str,
    seed_isrc: str,
    base_score: float,
) -> RecommendationCandidate | None:
    """Convert a ``_build_spotify_track_entry`` output into a candidate.

    Spotify items already carry ISRC + preview_url + cover_url; we don't
    hit Deezer here (no deezer_id available) — import goes through the
    ISRC-based pipeline which handles the Deezer resolution at that stage.
    """
    if not isinstance(item, dict):
        return None
    isrc = str(item.get("isrc") or "").upper()
    if not isrc:
        return None
    title = str(item.get("title") or "")
    artist = str(item.get("artist") or "")
    if not title or not artist:
        return None
    track = Track(
        isrc=isrc,
        title=title,
        artist=artist,
        album="",
        cover_url=str(item.get("cover_url") or ""),
        preview_url=str(item.get("preview_url") or ""),
    )
    return RecommendationCandidate(
        isrc=isrc,
        deezer_id=0,
        title=title,
        artist=artist,
        track=track,
        source=source,
        seed_isrc=seed_isrc,
        score=base_score,
        sources={source},
    )


def _lastfm_to_candidate(
    title: str,
    artist: str,
    match: float,
    *,
    source: str,
    seed_isrc: str,
    albums_store: Albums,
) -> RecommendationCandidate | None:
    """Search Deezer for a Last.fm candidate and produce a resolved candidate."""
    clean_title = (title or "").strip()
    clean_artist = (artist or "").strip()
    if not clean_title or not clean_artist:
        return None
    try:
        matches = search_track(clean_title, clean_artist)
    except Exception:  # noqa: BLE001
        return None
    if not matches:
        return None
    deezer_item = matches[0]
    album_id = int((deezer_item.get("album") or {}).get("id") or 0)
    try:
        album_data = fetch_album_preview(album_id, albums_store) if album_id else {}
    except Exception:  # noqa: BLE001
        album_data = {}
    track = build_track(deezer_item, album_data)
    isrc = (track.isrc or "").upper()
    if not isrc:
        return None
    return RecommendationCandidate(
        isrc=isrc,
        deezer_id=track.deezer_id,
        title=track.title,
        artist=track.artist,
        track=track,
        source=source,
        seed_isrc=seed_isrc,
        score=float(match) * 100.0,
        match=float(match),
        sources={source},
    )


# ── Seeds & helpers ──────────────────────────────────────────────────────────


def _recent_import_seeds(tracks_store: Tracks, *, limit: int) -> list[dict[str, Any]]:
    """Pick the most recent imports carrying an ISRC + deezer_id.

    We need ``deezer_id`` for Deezer's track-radio endpoint; entries
    without one are skipped rather than falling back to a search (that
    would happen synchronously and slow the whole feed).
    """
    eligible: list[tuple[str, dict[str, Any]]] = []
    for _apple_id, entry in tracks_store.all().items():
        if not isinstance(entry, dict):
            continue
        imported_at = str(entry.get("imported_at") or "").strip()
        isrc = str(entry.get("isrc") or "").upper()
        deezer_id = int(entry.get("deezer_id") or 0)
        if not imported_at or not isrc or not deezer_id:
            continue
        eligible.append((imported_at, entry))
    eligible.sort(key=lambda item: item[0], reverse=True)
    seeds: list[dict[str, Any]] = []
    for _imported, entry in eligible[:limit]:
        seeds.append(
            {
                "isrc": str(entry.get("isrc") or "").upper(),
                "title": str(entry.get("title") or ""),
                "artist": str(entry.get("artist") or ""),
                "cover_url": str(entry.get("cover_url") or ""),
                "deezer_id": int(entry.get("deezer_id") or 0),
            }
        )
    return seeds


def _empty_preview(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "creator": "",
        "nb_tracks": 0,
        "cover_url": "",
        "cover_thumb": "",
        "tracks": [],
        "skipped": 0,
    }


# ── Cache (TTL 30 min + signals.jsonl invalidation) ──────────────────────────


def _cache_path(paths: Paths) -> str:
    return os.path.join(paths.root, ".data", "reco_feed_cache.json")


def _load_cache(cache_path: str, signals_path: str, wanted: list[str]) -> dict[str, Any] | None:
    """Return a payload restricted to ``wanted`` if all sections are cached.

    The cache is additive across calls (see :func:`_write_cache`) so a
    widget doing progressive loading (one section per call) benefits
    from every prior response instead of destroying it.
    """
    payload = _read_cache_file(cache_path, signals_path)
    if payload is None:
        return None
    cached_sections = payload.get("sections") or []
    if not isinstance(cached_sections, list):
        return None
    by_id: dict[str, dict[str, Any]] = {
        str(s.get("id")): s for s in cached_sections if isinstance(s, dict) and s.get("id")
    }
    if set(wanted) - set(by_id.keys()):
        return None
    ordered = [by_id[name] for name in wanted]
    return {
        "generated_at": payload.get("generated_at", ""),
        "sections": ordered,
        "errors": payload.get("errors") or {},
        "cache_hit": True,
    }


def _read_cache_file(cache_path: str, signals_path: str) -> dict[str, Any] | None:
    """Load + validate the raw cache dict, or ``None`` when invalid/stale."""
    if not os.path.isfile(cache_path):
        return None
    try:
        cached_mtime = os.path.getmtime(cache_path)
    except OSError:
        return None
    if time.time() - cached_mtime > _CACHE_TTL_SECONDS:
        return None
    if os.path.isfile(signals_path):
        try:
            if os.path.getmtime(signals_path) > cached_mtime:
                return None
        except OSError:
            pass
    try:
        with open(cache_path, encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_cache(cache_path: str, payload: dict[str, Any], signals_path: str) -> None:
    """Merge the fresh payload's sections into the existing cache.

    Individual calls only regenerate the sections they were asked for;
    without a merge every progressive-load call would evict the sections
    the previous call cached, defeating the whole purpose of the cache.
    """
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    existing = _read_cache_file(cache_path, signals_path) or {}
    existing_sections = existing.get("sections") or []
    if not isinstance(existing_sections, list):
        existing_sections = []
    fresh_sections = payload.get("sections") or []
    if not isinstance(fresh_sections, list):
        fresh_sections = []
    by_id: dict[str, dict[str, Any]] = {}
    for section in existing_sections + fresh_sections:
        if isinstance(section, dict) and section.get("id"):
            by_id[str(section["id"])] = section
    merged = {
        "generated_at": payload.get("generated_at") or existing.get("generated_at", ""),
        "sections": list(by_id.values()),
        "errors": payload.get("errors") or {},
    }
    tmp = cache_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(merged, file, ensure_ascii=False)
        os.replace(tmp, cache_path)
    except OSError as exc:
        log_event("reco_feed_cache_write_failed", error=str(exc))


def _now_iso() -> str:
    from datetime import UTC, datetime  # noqa: PLC0415

    return datetime.now(UTC).isoformat(timespec="seconds")


# ── Mix cards — Spotify Home style grid ──────────────────────────────────────


_MOOD_MIXES: tuple[tuple[str, str], ...] = (
    ("chill", "Détente"),
    ("focus", "Focus"),
    ("party", "Fête"),
    ("sad", "Triste"),
    ("love", "Amour"),
    ("workout", "Sport"),
)
_MIXES_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 h — mix index changes only on library import


def build_mixes_index(
    paths: Paths,
    tracks_store: Tracks,
    signals: SignalsLog | None = None,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return the fast landing view: groups of clickable mix cards.

    No track resolution — each card only carries a title, subtitle and
    cover URL. The heavy lifting happens on click via ``resolve_mix``,
    which the widget triggers through ``recos-mix-tracks <kind> <value>``.
    """
    cache_path = _mixes_cache_path(paths)
    if not force_refresh:
        cached = _load_mixes_cache(cache_path, tracks_store)
        if cached is not None:
            cached["cache_hit"] = True
            return cached

    profile = build_profile(tracks_store.all(), mode="library")
    groups: list[dict[str, Any]] = []

    # 1. Radios seeded from the user's most recent imports.
    recent = _recent_import_seeds(tracks_store, limit=6)
    if recent:
        groups.append(
            {
                "id": "recent",
                "title": "Basé sur tes écoutes récentes",
                "cards": [
                    {
                        "kind": "track",
                        "value": seed["isrc"],
                        "title": f"Radio {seed['title']}",
                        "subtitle": seed["artist"],
                        "cover_url": seed["cover_url"],
                    }
                    for seed in recent
                ],
            }
        )

    # 2. One mix card per top artist — cover pulled from Deezer artist API.
    artist_cards = _artist_mix_cards(profile.top_artists[:6])
    if artist_cards:
        groups.append(
            {"id": "artists", "title": "Vos mix par artiste", "cards": artist_cards}
        )

    # 3. One mix card per top genre — cover sampled from a user track in
    #    that genre so it "feels" personal.
    genre_cards = _genre_mix_cards(profile.top_genres[:6], tracks_store)
    if genre_cards:
        groups.append({"id": "genres", "title": "Vos mix par genre", "cards": genre_cards})

    # 4. Decades represented in the library.
    decade_cards = _decade_mix_cards(tracks_store, limit=5)
    if decade_cards:
        groups.append(
            {"id": "decades", "title": "Vos mix par décennie", "cards": decade_cards}
        )

    # 5. Fixed mood palette — Last.fm tag radios.
    mood_cards = [
        {
            "kind": "mood",
            "value": tag,
            "title": f"Mix {label}",
            "subtitle": "",
            "cover_url": "",
        }
        for tag, label in _MOOD_MIXES
    ]
    groups.append({"id": "moods", "title": "Vos Mood Mixes", "cards": mood_cards})

    payload = {
        "generated_at": _now_iso(),
        "groups": groups,
        "cache_hit": False,
    }
    _write_mixes_cache(cache_path, payload)
    return payload


def resolve_mix(
    kind: str,
    value: str,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog | None = None,
) -> dict[str, Any]:
    """Resolve a mix card into a widget-ready track list.

    Dispatch by kind: ``track`` → ``build_track_radio``, ``artist`` →
    ``build_artist_radio``, ``genre`` → :func:`build_genre_mix`,
    ``mood`` → :func:`build_mood_mix`, ``decade`` → :func:`build_decade_mix`.
    """
    kind = (kind or "").strip().lower()
    value = (value or "").strip()
    if not kind or not value:
        return _empty_preview("Mix")
    if kind == "track":
        return build_track_radio(value, tracks_store, albums_store, recs_store, signals=signals)
    if kind == "artist":
        return build_artist_radio(value, tracks_store, albums_store, recs_store, signals=signals)
    if kind == "genre":
        return build_genre_mix(value, tracks_store, albums_store, recs_store, signals=signals)
    if kind == "mood":
        return build_mood_mix(value, tracks_store, albums_store, recs_store, signals=signals)
    if kind == "decade":
        try:
            decade = int(value)
        except ValueError:
            return _empty_preview("Mix")
        return build_decade_mix(decade, tracks_store, albums_store, recs_store, signals=signals)
    return _empty_preview("Mix")


def build_genre_mix(
    genre_name: str,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog | None = None,
) -> dict[str, Any]:
    """Deezer editorial chart for a genre + rerank against the user's taste."""
    if not genre_name:
        return _empty_preview("Mix genre")
    genre_id = deezer_reco.resolve_genre_id(genre_name)
    raw = deezer_reco.genre_charts(genre_id if genre_id else 0, limit=40)
    pool = _deezer_items_to_candidates(
        raw, source="deezer_genre_chart", seed_isrc="", albums_store=albums_store
    )
    profile = build_profile(tracks_store.all(), mode="library")
    ranked = rerank_for_section(
        pool,
        profile,
        tracks_store,
        recs_store,
        signals=signals,
        mode="library",
        max_per_artist=1,
        limit=25,
    )
    return {
        "name": f"Mix {genre_name}",
        "creator": "",
        "nb_tracks": len(ranked),
        "cover_url": _sample_cover_for_genre(genre_name, tracks_store),
        "cover_thumb": "",
        "tracks": [candidate_to_widget_track(c, tracks_store=tracks_store) for c in ranked],
        "skipped": 0,
    }


def build_mood_mix(
    tag: str,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog | None = None,
) -> dict[str, Any]:
    """Last.fm tag radio (chill / focus / party …) reranked for the user."""
    label = next((lbl for tg, lbl in _MOOD_MIXES if tg == tag), tag.capitalize())
    if not lastfm.get_api_key():
        return _empty_preview(f"Mix {label}")
    raw = lastfm.get_top_tracks_by_tag(tag, limit=60)
    pool: list[RecommendationCandidate] = []
    with ThreadPoolExecutor(max_workers=_RESOLVE_WORKERS) as pool_exec:
        futures = [
            pool_exec.submit(
                _lastfm_to_candidate,
                str(item.get("name") or ""),
                str(item.get("artist") or ""),
                float(item.get("match") or 0.5),
                source="lastfm_tag",
                seed_isrc="",
                albums_store=albums_store,
            )
            for item in raw
        ]
        for future in futures:
            cand = future.result()
            if cand:
                pool.append(cand)
    profile = build_profile(tracks_store.all(), mode="library")
    ranked = rerank_for_section(
        pool,
        profile,
        tracks_store,
        recs_store,
        signals=signals,
        mode="library",
        max_per_artist=1,
        limit=25,
    )
    return {
        "name": f"Mix {label}",
        "creator": "",
        "nb_tracks": len(ranked),
        "cover_url": "",
        "cover_thumb": "",
        "tracks": [candidate_to_widget_track(c, tracks_store=tracks_store) for c in ranked],
        "skipped": 0,
    }


def build_decade_mix(
    decade: int,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    signals: SignalsLog | None = None,
) -> dict[str, Any]:
    """Mix of user's tracks from ``decade`` + Deezer track-radio expansion.

    Take the user's own tracks from that decade (the "you already like this"
    core), expand via Deezer track-radio on the top 2-3 seeds, blend, rerank.
    Gives a "familiar with fresh discoveries" feel instead of a static
    filter over the library.
    """
    own = _tracks_in_decade(tracks_store, decade)
    if not own:
        return _empty_preview(f"Mix Années {decade}")

    own.sort(key=lambda e: int(e.get("play_count") or 0), reverse=True)
    raw_items: list[dict[str, Any]] = []
    for entry in own[:3]:
        deezer_id = int(entry.get("deezer_id") or 0)
        if not deezer_id:
            continue
        raw_items.extend(deezer_reco.track_radio(deezer_id))
    radio_pool = _deezer_items_to_candidates(
        raw_items,
        source="deezer_track_radio",
        seed_isrc="",
        albums_store=albums_store,
    )
    profile = build_profile(tracks_store.all(), mode="library")
    ranked = rerank_for_section(
        radio_pool,
        profile,
        tracks_store,
        recs_store,
        signals=signals,
        mode="library",
        max_per_artist=1,
        limit=25,
    )
    return {
        "name": f"Mix Années {decade}",
        "creator": "",
        "nb_tracks": len(ranked),
        "cover_url": _sample_cover_from_entries(own),
        "cover_thumb": "",
        "tracks": [candidate_to_widget_track(c, tracks_store=tracks_store) for c in ranked],
        "skipped": 0,
    }


# ── Mix cover / metadata helpers ─────────────────────────────────────────────


def _artist_mix_cards(top_artists: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """Fetch Deezer artist portraits in parallel."""

    def gather(item: tuple[str, int]) -> dict[str, Any] | None:
        artist_name, _score = item
        aid = deezer_reco.resolve_artist_id(artist_name)
        cover = ""
        if aid:
            data = deezer_get(f"/artist/{aid}")
            if data and "error" not in data:
                cover = (
                    str(data.get("picture_xl") or "")
                    or str(data.get("picture_big") or "")
                    or str(data.get("picture_medium") or "")
                )
        return {
            "kind": "artist",
            "value": artist_name,
            "title": f"Mix {artist_name}",
            "subtitle": "",
            "cover_url": cover,
        }

    if not top_artists:
        return []
    with ThreadPoolExecutor(max_workers=min(_RESOLVE_WORKERS, len(top_artists))) as pool_exec:
        results = list(pool_exec.map(gather, top_artists))
    return [c for c in results if c is not None]


def _genre_mix_cards(
    top_genres: list[tuple[str, int]], tracks_store: Tracks
) -> list[dict[str, Any]]:
    return [
        {
            "kind": "genre",
            "value": name,
            "title": f"Mix {name}",
            "subtitle": "",
            "cover_url": _sample_cover_for_genre(name, tracks_store),
        }
        for name, _count in top_genres
    ]


def _decade_mix_cards(tracks_store: Tracks, *, limit: int) -> list[dict[str, Any]]:
    counter: dict[int, list[dict[str, Any]]] = {}
    for entry in tracks_store.all().values():
        if not isinstance(entry, dict):
            continue
        year = _year_of(str(entry.get("release_date") or ""))
        if not year:
            continue
        decade = (year // 10) * 10
        counter.setdefault(decade, []).append(entry)
    sorted_decades = sorted(counter.items(), key=lambda kv: -len(kv[1]))[:limit]
    cards: list[dict[str, Any]] = []
    for decade, entries in sorted_decades:
        cards.append(
            {
                "kind": "decade",
                "value": str(decade),
                "title": f"Mix Années {decade}",
                "subtitle": f"{len(entries)} morceaux dans ta bibliothèque",
                "cover_url": _sample_cover_from_entries(entries),
            }
        )
    return cards


def _tracks_in_decade(tracks_store: Tracks, decade: int) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for entry in tracks_store.all().values():
        if not isinstance(entry, dict):
            continue
        year = _year_of(str(entry.get("release_date") or ""))
        if year and (year // 10) * 10 == decade:
            kept.append(entry)
    return kept


def _year_of(date_str: str) -> int | None:
    if not date_str:
        return None
    prefix = date_str[:4]
    if len(prefix) != 4 or not prefix.isdigit():
        return None
    try:
        return int(prefix)
    except ValueError:
        return None


def _sample_cover_for_genre(genre_name: str, tracks_store: Tracks) -> str:
    target = genre_name.lower().strip()
    best: tuple[int, str] | None = None
    for entry in tracks_store.all().values():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("genre") or "").lower().strip() != target:
            continue
        cover = str(entry.get("cover_url") or "")
        if not cover:
            continue
        playcount = int(entry.get("play_count") or 0)
        if best is None or playcount > best[0]:
            best = (playcount, cover)
    return best[1] if best else ""


def _sample_cover_from_entries(entries: list[dict[str, Any]]) -> str:
    for entry in sorted(entries, key=lambda e: -int(e.get("play_count") or 0)):
        cover = str(entry.get("cover_url") or "")
        if cover:
            return cover
    return ""


# ── Mixes-index cache ────────────────────────────────────────────────────────


def _mixes_cache_path(paths: Paths) -> str:
    return os.path.join(paths.root, ".data", "reco_mixes_cache.json")


def _load_mixes_cache(cache_path: str, tracks_store: Tracks) -> dict[str, Any] | None:
    if not os.path.isfile(cache_path):
        return None
    try:
        cached_mtime = os.path.getmtime(cache_path)
    except OSError:
        return None
    if time.time() - cached_mtime > _MIXES_CACHE_TTL_SECONDS:
        return None
    # Invalidate when tracks.json has been touched — a fresh import can
    # bump a new artist into the top-6 or add a new decade.
    try:
        if os.path.getmtime(getattr(tracks_store, "_path", "") or cache_path) > cached_mtime:
            return None
    except OSError:
        pass
    try:
        with open(cache_path, encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_mixes_cache(cache_path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    tmp = cache_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False)
        os.replace(tmp, cache_path)
    except OSError as exc:
        log_event("reco_mixes_cache_write_failed", error=str(exc))
