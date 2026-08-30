"""Shared scoring / dedup / blend for the recommendation pipelines.

Two consumers today:

- ``pipeline.recommend`` — batch generation (auto-import a full playlist).
- ``pipeline.ecosystem`` — Spotify-Home style feed rendered by the widget
  (preview-first, cherry-pick).

The scoring bonuses (genre, artist, playcount, recency, affinity,
discovery), the Deezer parallel resolution and the artist-diversification
live here so both consumers keep the same personalization surface.

Multi-source blend (``blend_candidates``) is what makes the ecosystem
feed work: candidates coming from Deezer / Spotify / Last.fm are merged
by ISRC and a track confirmed by two sources gets a boost — the more
sources agree, the higher it ranks.
"""

import math
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from music_manager.core.models import Track
from music_manager.core.profile import Profile
from music_manager.pipeline.dedup import is_duplicate
from music_manager.services.albums import Albums
from music_manager.services.recommendations_store import RecommendationsStore
from music_manager.services.resolver import build_track, fetch_album_preview, search_track
from music_manager.services.signals import SignalsLog
from music_manager.services.tracks import Tracks

# ── Constants ────────────────────────────────────────────────────────────────

_DEEZER_RESOLVE_WORKERS = 8

# Personalization bonuses — kept identical to the legacy recommend.py values
# so behaviour of the existing batch pipeline stays byte-for-byte the same.
_GENRE_BONUS = 12.0
_ARTIST_BONUS = 6.0
_LOCAL_ARTIST_PLAYCOUNT_BONUS_MAX = 18.0
_RECENT_RELEASE_BONUS_MAX = 18.0
_RECENT_RELEASE_DAYS = 365
_PLAYCOUNT_LOG_BONUS_MAX = 25.0

_MAX_TRACKS_PER_ARTIST = 2

_AFFINITY_ARTIST_BONUS = 15.0
_AFFINITY_ARTIST_MALUS = 20.0
_AFFINITY_GENRE_BONUS = 10.0
_AFFINITY_GENRE_MALUS = 15.0
_AFFINITY_ARTIST_POS_THRESHOLD = 0.5
_AFFINITY_ARTIST_NEG_THRESHOLD = -0.3
_AFFINITY_GENRE_POS_THRESHOLD = 0.5
_AFFINITY_GENRE_NEG_THRESHOLD = -0.3

_DISCOVERY_FAMILIARITY_MALUS = 10.0
_DISCOVERY_COLD_ARTIST_BONUS = 20.0

# Multi-source blend — each additional source confirms the pick and adds
# up to ``_BLEND_BONUS_MAX``. A track surfaced by Deezer + Spotify + Last.fm
# thus gets +30 vs. a track surfaced by a single API.
_BLEND_BONUS_PER_SOURCE = 10.0
_BLEND_BONUS_MAX = 30.0


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class RecommendationCandidate:
    """A Last.fm / Deezer / Spotify candidate resolved on Deezer for import."""

    isrc: str
    deezer_id: int
    title: str
    artist: str
    track: Track
    source: str
    seed_isrc: str
    score: float
    match: float = 0.0
    playcount: int = 0
    sources: set[str] = field(default_factory=set)


# ── Resolution ───────────────────────────────────────────────────────────────


def resolve_candidates(
    candidates: list[dict[str, Any]],
    albums_store: Albums,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list[RecommendationCandidate]:
    """Search Deezer for each raw candidate. Drop those that don't resolve.

    Input dict shape (any missing key is treated as absent):
        {"name": str, "artist": str, "match": float, "playcount": int,
         "source": str, "seed_isrc": str}

    Parallelised (``_DEEZER_RESOLVE_WORKERS`` workers) because Deezer
    ``/search/track`` is HTTP-bound and each seed can yield 30-50 candidates.
    """
    total = len(candidates)
    resolved: list[RecommendationCandidate] = []
    completed = 0

    def worker(payload: dict[str, Any]) -> RecommendationCandidate | None:
        if not payload.get("name"):
            return None
        try:
            matches = search_track(payload["name"], payload["artist"])
        except Exception:  # noqa: BLE001
            return None
        if not matches:
            return None
        deezer_item = matches[0]
        album_id = deezer_item.get("album", {}).get("id", 0)
        try:
            album_data = fetch_album_preview(album_id, albums_store)
        except Exception:  # noqa: BLE001
            return None
        track = build_track(deezer_item, album_data)
        isrc = (track.isrc or "").upper()
        if not isrc:
            return None
        source = payload.get("source", "")
        return RecommendationCandidate(
            isrc=isrc,
            deezer_id=track.deezer_id,
            title=track.title,
            artist=track.artist,
            track=track,
            source=source,
            seed_isrc=payload.get("seed_isrc", ""),
            score=float(payload.get("match", 0.0)) * 100.0,
            match=float(payload.get("match", 0.0)),
            playcount=int(payload.get("playcount", 0)),
            sources={source} if source else set(),
        )

    with ThreadPoolExecutor(max_workers=_DEEZER_RESOLVE_WORKERS) as pool:
        futures = [pool.submit(worker, item) for item in candidates]
        for future in as_completed(futures):
            completed += 1
            outcome = future.result()
            if outcome is not None:
                resolved.append(outcome)
            if on_progress:
                on_progress("resolve", completed, total)

    return resolved


# ── Multi-source blend ───────────────────────────────────────────────────────


def blend_candidates(
    pools: list[list[RecommendationCandidate]],
) -> list[RecommendationCandidate]:
    """Merge candidate pools coming from different sources by ISRC.

    When the same track appears in multiple pools we keep the highest raw
    score, union the source labels, and apply a ``+10 per additional source``
    bonus capped at ``+30``. Cross-source confirmation is a stronger signal
    than any single API's ranking.
    """
    merged: dict[str, RecommendationCandidate] = {}
    for pool in pools:
        for candidate in pool:
            key = (candidate.isrc or "").upper()
            if not key:
                continue
            existing = merged.get(key)
            if existing is None:
                merged[key] = candidate
                if candidate.source:
                    candidate.sources.add(candidate.source)
                continue
            if candidate.score > existing.score:
                existing.score = candidate.score
            if candidate.match > existing.match:
                existing.match = candidate.match
            if candidate.playcount > existing.playcount:
                existing.playcount = candidate.playcount
            if candidate.source:
                existing.sources.add(candidate.source)

    for candidate in merged.values():
        extra = max(0, len(candidate.sources) - 1)
        candidate.score += min(extra * _BLEND_BONUS_PER_SOURCE, _BLEND_BONUS_MAX)
    return list(merged.values())


# ── Dedup + rank ─────────────────────────────────────────────────────────────


def dedup_and_rank(
    resolved: list[RecommendationCandidate],
    profile: Profile,
    tracks_store: Tracks,
    recs_store: RecommendationsStore,
    signals: SignalsLog | None = None,
    mode: str = "library",
    *,
    max_per_artist: int = _MAX_TRACKS_PER_ARTIST,
    skip_active: bool = True,
    skip_library: bool = True,
) -> tuple[list[RecommendationCandidate], dict[str, int]]:
    """Apply personalization bonuses then diversify by artist.

    ``skip_active`` / ``skip_library`` let ecosystem callers keep the feed
    predictable even for users whose library already covers the seed:
    when both are False you get a pure "what would this section look like
    if I didn't own any of it" list, useful for the "For you" home cards.

    Scoring on top of the base score already carried by the candidate:
    - +12 if the candidate genre is a top user genre
    - +6  if the candidate artist is a top user artist
    - up to +18 for artists the user plays a lot locally
    - up to +18 for recent releases (linear decay over one year)
    - up to +25 from a log-scaled Last.fm playcount
    - ±15 / ±20 from artist affinity (over 180-day window)
    - ±10 / ±15 from genre affinity
    - discovery mode: −10 for known artists, +20 for cold artists
    """
    top_genres = {name.lower() for name, _count in profile.top_genres}
    top_artists = {name.lower() for name, _score in profile.top_artists}
    local_artist_playcounts = _local_artist_playcounts(tracks_store)
    artist_affinity = signals.artist_affinity() if signals else {}
    genre_affinity = signals.genre_affinity() if signals else {}
    is_discovery = mode == "discovery"
    known_artists: set[str] = set()
    if is_discovery:
        known_artists = {
            str(entry.get("artist") or "").lower() for entry in tracks_store.all().values()
        }
        known_artists.discard("")
    counters = {"blacklist": 0, "active": 0, "library": 0, "empty_isrc": 0}
    seen_isrcs: set[str] = set()
    kept: list[RecommendationCandidate] = []

    for candidate in resolved:
        if not candidate.isrc:
            counters["empty_isrc"] += 1
            continue
        if candidate.isrc in seen_isrcs:
            continue
        seen_isrcs.add(candidate.isrc)

        if recs_store.is_blacklisted(candidate.isrc):
            counters["blacklist"] += 1
            continue
        if skip_active and recs_store.is_active(candidate.isrc):
            counters["active"] += 1
            continue
        if skip_library and is_duplicate(
            candidate.isrc, candidate.title, candidate.artist, tracks_store
        ):
            counters["library"] += 1
            continue

        if candidate.track.genre and candidate.track.genre.lower() in top_genres:
            candidate.score += _GENRE_BONUS
        if candidate.artist and candidate.artist.lower() in top_artists:
            candidate.score += _ARTIST_BONUS
        _apply_local_artist_playcount_bonus(candidate, local_artist_playcounts)
        _apply_recent_release_bonus(candidate)
        if candidate.playcount > 0:
            candidate.score += min(
                math.log10(candidate.playcount) * 3.5, _PLAYCOUNT_LOG_BONUS_MAX
            )

        _apply_affinity(candidate, artist_affinity, genre_affinity)

        if is_discovery:
            _apply_discovery_bonuses(candidate, top_artists, known_artists)

        kept.append(candidate)

    kept.sort(key=lambda item: item.score, reverse=True)
    return _diversify_by_artist(kept, max_per_artist=max_per_artist), counters


def rerank_for_section(
    candidates: list[RecommendationCandidate],
    profile: Profile,
    tracks_store: Tracks,
    recs_store: RecommendationsStore,
    signals: SignalsLog | None = None,
    *,
    mode: str = "library",
    max_per_artist: int = 1,
    limit: int = 10,
) -> list[RecommendationCandidate]:
    """Wrap ``dedup_and_rank`` for the ecosystem feed.

    Ecosystem sections don't care about the counters and always skip
    library/blacklist. They usually want a *mixed* view (one track per
    artist) except for per-artist sections that want deeper cuts.
    """
    ranked, _counters = dedup_and_rank(
        candidates,
        profile,
        tracks_store,
        recs_store,
        signals=signals,
        mode=mode,
        max_per_artist=max_per_artist,
        skip_active=False,
        skip_library=True,
    )
    return ranked[:limit]


# ── Private helpers ──────────────────────────────────────────────────────────


def _local_artist_playcounts(tracks_store: Tracks) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in tracks_store.all().values():
        artist = str(entry.get("artist") or "").strip().lower()
        if not artist:
            continue
        try:
            play_count = int(entry.get("play_count") or 0)
        except (TypeError, ValueError):
            play_count = 0
        if play_count <= 0:
            continue
        counts[artist] = counts.get(artist, 0) + play_count
    return counts


def _apply_local_artist_playcount_bonus(
    candidate: RecommendationCandidate,
    local_artist_playcounts: dict[str, int],
) -> None:
    if not candidate.artist:
        return
    play_count = local_artist_playcounts.get(candidate.artist.lower(), 0)
    if play_count <= 0:
        return
    candidate.score += min(
        math.log1p(play_count) * 4.0,
        _LOCAL_ARTIST_PLAYCOUNT_BONUS_MAX,
    )


def _apply_recent_release_bonus(
    candidate: RecommendationCandidate,
    *,
    now: datetime | None = None,
) -> None:
    release_date = (candidate.track.release_date or "").strip()
    if not release_date:
        return
    released_at = _parse_release_date(release_date)
    if released_at is None:
        return
    current = now or datetime.now(UTC)
    if released_at.tzinfo is None:
        released_at = released_at.replace(tzinfo=UTC)
    age_days = max(0, (current - released_at).days)
    if age_days > _RECENT_RELEASE_DAYS:
        return
    freshness = 1.0 - (age_days / _RECENT_RELEASE_DAYS)
    candidate.score += _RECENT_RELEASE_BONUS_MAX * freshness


def _parse_release_date(value: str) -> datetime | None:
    for fmt, width in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            return datetime.strptime(value[:width], fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _apply_discovery_bonuses(
    candidate: RecommendationCandidate,
    top_artists: set[str],
    known_artists: set[str],
) -> None:
    artist_key = candidate.artist.lower()
    if not artist_key:
        return
    if artist_key in top_artists:
        candidate.score -= _DISCOVERY_FAMILIARITY_MALUS
    if artist_key not in known_artists:
        candidate.score += _DISCOVERY_COLD_ARTIST_BONUS


def _apply_affinity(
    candidate: RecommendationCandidate,
    artist_affinity: dict[str, float],
    genre_affinity: dict[str, float],
) -> None:
    if candidate.artist:
        score = artist_affinity.get(candidate.artist.lower())
        if score is not None:
            if score >= _AFFINITY_ARTIST_POS_THRESHOLD:
                candidate.score += _AFFINITY_ARTIST_BONUS
            elif score <= _AFFINITY_ARTIST_NEG_THRESHOLD:
                candidate.score -= _AFFINITY_ARTIST_MALUS
    genre = (candidate.track.genre or "").lower()
    if genre:
        score = genre_affinity.get(genre)
        if score is not None:
            if score >= _AFFINITY_GENRE_POS_THRESHOLD:
                candidate.score += _AFFINITY_GENRE_BONUS
            elif score <= _AFFINITY_GENRE_NEG_THRESHOLD:
                candidate.score -= _AFFINITY_GENRE_MALUS


def _diversify_by_artist(
    candidates: list[RecommendationCandidate],
    *,
    max_per_artist: int = _MAX_TRACKS_PER_ARTIST,
) -> list[RecommendationCandidate]:
    per_artist: dict[str, int] = {}
    diversified: list[RecommendationCandidate] = []
    overflow: list[RecommendationCandidate] = []
    for candidate in candidates:
        key = candidate.artist.lower()
        if per_artist.get(key, 0) < max_per_artist:
            diversified.append(candidate)
            per_artist[key] = per_artist.get(key, 0) + 1
        else:
            overflow.append(candidate)
    diversified.extend(overflow)
    return diversified


# ── Widget-ready serialization ───────────────────────────────────────────────


def candidate_to_widget_track(
    candidate: RecommendationCandidate,
    *,
    tracks_store: Tracks | None = None,
) -> dict[str, Any]:
    """Project a candidate onto the shape consumed by ``dashboard.jsx``.

    Same schema as ``cli.search._format_track`` so the widget can reuse
    its existing row / preview components. ``in_library`` is derived from
    ``tracks_store`` when provided (recos already-owned still show up but
    with a passive icon instead of the "+" button).
    """
    apple_id = ""
    in_library = False
    if tracks_store is not None and candidate.isrc:
        existing = tracks_store.get_by_isrc(candidate.isrc)
        if existing:
            apple_id = str(existing.get("apple_id") or "")
            in_library = True
    track = candidate.track
    return {
        "isrc": candidate.isrc,
        "title": track.title or candidate.title,
        "artist": track.artist or candidate.artist,
        "album": track.album,
        "deezer_id": int(track.deezer_id or candidate.deezer_id or 0),
        "duration": int(track.duration or 0),
        "preview_url": track.preview_url or "",
        "cover_url": track.cover_url or "",
        "explicit": bool(track.explicit),
        "in_library": in_library,
        "apple_id": apple_id,
        "sources": sorted(candidate.sources),
        "score": round(candidate.score, 2),
    }


# ── Small utils (shared by ecosystem for name sanitisation) ──────────────────


def slugify(value: str, *, max_length: int = 50) -> str:
    """Lowercase ASCII slug — used by the ecosystem cache keys."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned: list[str] = []
    for char in ascii_value.lower():
        if char in (" ", "\t", "\n", "/", "\\", '"', "'"):
            cleaned.append("-")
        elif char.isalnum() or char == "-":
            cleaned.append(char)
    collapsed = "".join(cleaned)
    while "--" in collapsed:
        collapsed = collapsed.replace("--", "-")
    return collapsed.strip("-")[:max_length]
