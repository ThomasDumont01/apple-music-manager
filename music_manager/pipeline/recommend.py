"""Recommendations pipeline.

End-to-end orchestration:

A. Scan: diff Apple Music "Recommandations" playlist ↔ store → blacklist
   tracks the user has removed since the last run.
B. Profile: aggregate user signals over tracks.json.
C. Candidates: query Last.fm (track.getSimilar for seeds, tag.getTopTracks
   for moods; artist.getSimilar as fallback when seeds run dry).
D. Resolve: search each candidate on Deezer → ISRC + Track. Drop misses.
E. Dedup: blacklist > active > library > empty-ISRC.
F. Rank: Last.fm match boosted by genre/artist affinity.
G. Import: reuse pipeline.importer.import_resolved_track().
H. Playlist: add successes to "Recommandations".
I. Save: stores + stats.

Errors during a single import never abort the run — they are counted and
reported via :class:`GenerationResult`.
"""

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from music_manager.core.config import Paths
from music_manager.core.logger import log_event
from music_manager.core.models import Track
from music_manager.core.profile import Profile, build_profile
from music_manager.pipeline.dedup import is_duplicate
from music_manager.pipeline.importer import import_resolved_track
from music_manager.services import apple, lastfm
from music_manager.services.albums import Albums
from music_manager.services.recommendations_store import RecommendationsStore
from music_manager.services.resolver import build_track, fetch_album_with_cover, search_track
from music_manager.services.tracks import Tracks

# ── Constants ────────────────────────────────────────────────────────────────

PLAYLIST_NAME = "for me"
MOOD_TAGS = ("chill", "energetic", "melancholic", "romantic", "party", "focus")
DEFAULT_TARGET = 20

_SEED_TRACK_COUNT = 50
_SEED_FALLBACK_TRACK_COUNT = 50
_MIN_CANDIDATE_POOL = 50
_DEEZER_RESOLVE_WORKERS = 8
_TOP_ARTIST_FALLBACK_LIMIT = 5
_GENRE_BONUS = 12.0
_ARTIST_BONUS = 6.0

# Quality filters / boosts
_MIN_LASTFM_MATCH = 0.30
_PLAYCOUNT_LOG_BONUS_MAX = 25.0  # bonus capped at +25 for ~10M playcount
_MAX_TRACKS_PER_ARTIST = 2

# Negative reinforcement: skip seeds whose past picks were mostly blacklisted.
_SEED_BLACKLIST_RATIO_THRESHOLD = 0.5


# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class RecommendationCandidate:
    """A Last.fm candidate already resolved on Deezer (ready to import)."""

    isrc: str
    deezer_id: int
    title: str
    artist: str
    track: Track
    source: str  # "lastfm_similar" | "lastfm_tag" | "lastfm_artist_similar"
    seed_isrc: str
    score: float
    match: float = 0.0  # raw Last.fm similarity (0-1)
    playcount: int = 0  # Last.fm global play count


@dataclass
class GenerationResult:
    """Outcome of a generate_recommendations() run."""

    imported: int = 0
    failed: int = 0
    skipped_blacklist: int = 0
    skipped_in_library: int = 0
    skipped_already_active: int = 0
    candidates_total: int = 0
    deleted_blacklisted: int = 0
    error: str = ""
    imported_isrcs: list[str] = field(default_factory=list)


# ── Entry point ──────────────────────────────────────────────────────────────


def generate_recommendations(
    *,
    mode: str,
    paths: Paths,
    tracks_store: Tracks,
    albums_store: Albums,
    recs_store: RecommendationsStore,
    target_count: int = DEFAULT_TARGET,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> GenerationResult:
    """Generate and import recommendations.

    ``mode`` is one of: ``"general"``, ``"genre:<name>"``, ``"mood:<tag>"``.

    ``on_progress(phase, current, total)`` is called between phases:
    ``scan`` (after deletion sync), ``candidates`` (per Last.fm batch),
    ``resolve`` (per Deezer search), ``import`` (per track), and ``done``.
    """
    result = GenerationResult()

    if not lastfm.get_api_key():
        result.error = "lastfm_no_api_key"
        log_event("recommend_no_api_key", mode=mode)
        return result

    # A. Scan playlist → blacklist deleted tracks.
    deleted = scan_deleted(recs_store, playlist_name=PLAYLIST_NAME)
    result.deleted_blacklisted = deleted
    if on_progress:
        on_progress("scan", deleted, deleted)

    # B. Profile.
    profile = build_profile(tracks_store.all(), mode=mode)

    # C. Candidates (using local seed-quality memory).
    seed_blacklist_ratio = recs_store.seed_quality()
    seeds = _collect_lastfm_candidates(profile, mode, seed_blacklist_ratio, on_progress)
    if not seeds:
        result.error = result.error or "lastfm_empty"
        log_event("recommend_no_candidates", mode=mode)
        return result
    result.candidates_total = len(seeds)

    # D. Resolve on Deezer (parallel).
    resolved = _resolve_candidates(seeds, albums_store, on_progress)

    # E. Dedup + rank.
    kept, counters = _dedup_and_rank(resolved, profile, tracks_store, recs_store)
    result.skipped_blacklist = counters["blacklist"]
    result.skipped_already_active = counters["active"]
    result.skipped_in_library = counters["library"]

    top = kept[:target_count]
    if on_progress:
        on_progress("import", 0, len(top))

    # G. Import sequentially through the existing pipeline.
    apple_ids: list[str] = []
    for idx, candidate in enumerate(top, start=1):
        pending = import_resolved_track(
            candidate.track,
            paths,
            tracks_store,
            albums_store,
        )
        if pending is None and candidate.track.apple_id:
            apple_ids.append(candidate.track.apple_id)
            recs_store.add_active(
                {
                    "isrc": candidate.isrc,
                    "apple_id": candidate.track.apple_id,
                    "title": candidate.title,
                    "artist": candidate.artist,
                    "source": candidate.source,
                    "seed_isrc": candidate.seed_isrc,
                    "score": candidate.score,
                    "mode": mode,
                }
            )
            result.imported += 1
            result.imported_isrcs.append(candidate.isrc)
            # Crash-safe: persist after each success so a network drop
            # mid-run doesn't lose the mapping the next scan_deleted needs.
            recs_store.save()
        else:
            result.failed += 1
        if on_progress:
            on_progress("import", idx, len(top))

    # H. Playlist sync.
    if apple_ids:
        try:
            apple.add_to_playlist(PLAYLIST_NAME, apple_ids)
        except Exception as exc:  # noqa: BLE001
            log_event("recommend_playlist_failed", error=str(exc))

    # I. Final save + stats.
    recs_store.record_generation()
    recs_store.save()
    tracks_store.save()
    albums_store.save()

    log_event(
        "recommend_done",
        mode=mode,
        imported=result.imported,
        failed=result.failed,
        blacklisted=deleted,
    )

    if on_progress:
        on_progress("done", result.imported, target_count)

    return result


def scan_deleted(
    recs_store: RecommendationsStore, *, playlist_name: str = PLAYLIST_NAME
) -> int:
    """Compare active recs with the current playlist and blacklist missing ones.

    Returns the number of tracks newly blacklisted.

    Edge cases:
    - Playlist absent and active not empty → ambiguous (user may have
      deleted the playlist entirely, or this may be the first run after a
      manual rename). We do NOT mass-blacklist; we log and skip.
    - Playlist absent and active empty → nothing to do.
    """
    active = recs_store.all_active()
    if not active:
        return 0

    try:
        current = set(apple.get_playlist_tracks(playlist_name))
    except Exception as exc:  # noqa: BLE001
        log_event("recommend_scan_failed", error=str(exc))
        return 0

    if not current:
        playlists = {name for name, _count in apple.list_playlists()}
        if playlist_name not in playlists:
            log_event("recommend_playlist_missing", playlist=playlist_name)
            return 0

    missing = {
        isrc for isrc, entry in active.items() if entry.get("apple_id") not in current
    }
    if not missing:
        return 0

    moved = recs_store.move_to_blacklist(missing)
    log_event("recommend_blacklisted", count=moved)
    return moved


# ── Private Functions ────────────────────────────────────────────────────────


def _collect_lastfm_candidates(
    profile: Profile,
    mode: str,
    seed_blacklist_ratio: dict[str, float],
    on_progress: Callable[[str, int, int], None] | None,
) -> list[dict[str, Any]]:
    """Run Last.fm queries and produce a deduped list of raw candidates.

    ``seed_blacklist_ratio`` is the negative-reinforcement input: seeds whose
    past picks were mostly blacklisted are skipped so the next batch leans
    on better-performing seeds.
    """
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    if mode.startswith("mood:"):
        tag = mode.split(":", 1)[1].strip()
        for item in lastfm.get_top_tracks_by_tag(tag, limit=200):
            _append_unique(
                candidates,
                seen,
                item,
                source="lastfm_tag",
                seed_isrc="",
            )
        if on_progress:
            on_progress("candidates", len(candidates), len(candidates))
        return candidates

    all_seeds = profile.top_tracks[:_SEED_TRACK_COUNT]
    seeds = [
        (isrc, title, artist)
        for isrc, title, artist in all_seeds
        if seed_blacklist_ratio.get(isrc.upper(), 0.0) < _SEED_BLACKLIST_RATIO_THRESHOLD
    ]
    skipped = len(all_seeds) - len(seeds)
    if skipped:
        log_event("recommend_seeds_skipped_by_reinforcement", count=skipped)
    if not seeds:
        # All seeds failed historically — fall back to the unfiltered list
        # rather than returning empty (so the user still gets something).
        seeds = all_seeds

    for idx, (seed_isrc, seed_title, seed_artist) in enumerate(seeds, start=1):
        for item in lastfm.get_similar_tracks(seed_artist, seed_title, limit=50):
            _append_unique(
                candidates,
                seen,
                item,
                source="lastfm_similar",
                seed_isrc=seed_isrc,
            )
        if on_progress:
            on_progress("candidates", idx, len(seeds))

    # Fallback: widen via artist.getSimilar when the seed pool is thin.
    if len(candidates) < _MIN_CANDIDATE_POOL and profile.top_artists:
        for artist_name, _score in profile.top_artists[:_TOP_ARTIST_FALLBACK_LIMIT]:
            for similar in lastfm.get_similar_artists(artist_name, limit=10):
                similar_name = similar.get("name") or ""
                if not similar_name:
                    continue
                # Use track.getSimilar of one popular track per artist if we knew
                # it — without scrobbling we can't, so we ride on tag-based
                # widening: pull artist top tracks from a generic tag stays
                # complicated, so we simply expand seeds. The Deezer search
                # at the next step will validate availability.
                _append_unique(
                    candidates,
                    seen,
                    {"name": "", "artist": similar_name, "match": similar.get("match", 0.0)},
                    source="lastfm_artist_similar",
                    seed_isrc="",
                )

    return candidates


def _append_unique(
    candidates: list[dict[str, Any]],
    seen: set[tuple[str, str]],
    item: dict[str, Any],
    *,
    source: str,
    seed_isrc: str,
) -> None:
    name = (item.get("name") or "").strip()
    artist = (item.get("artist") or "").strip()
    if not artist:
        return
    match = float(item.get("match") or 0.0)
    # Drop low-confidence picks early — Last.fm's match < 0.30 is mostly noise.
    if source == "lastfm_similar" and match < _MIN_LASTFM_MATCH:
        return
    key = (name.lower(), artist.lower())
    if key in seen:
        return
    seen.add(key)
    candidates.append(
        {
            "name": name,
            "artist": artist,
            "match": match,
            "playcount": int(item.get("playcount") or 0),
            "source": source,
            "seed_isrc": seed_isrc,
        }
    )


def _resolve_candidates(
    candidates: list[dict[str, Any]],
    albums_store: Albums,
    on_progress: Callable[[str, int, int], None] | None,
) -> list[RecommendationCandidate]:
    """Search Deezer for each Last.fm candidate. Drop those that don't map."""
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
            album_data = fetch_album_with_cover(album_id, albums_store)
        except Exception:  # noqa: BLE001
            return None
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
            source=payload["source"],
            seed_isrc=payload["seed_isrc"],
            score=float(payload["match"]) * 100.0,
            match=float(payload["match"]),
            playcount=int(payload.get("playcount", 0)),
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


def _dedup_and_rank(
    resolved: list[RecommendationCandidate],
    profile: Profile,
    tracks_store: Tracks,
    recs_store: RecommendationsStore,
) -> tuple[list[RecommendationCandidate], dict[str, int]]:
    """Apply dedup short-circuit, score boosts, then diversify by artist.

    Scoring: ``match * 100`` is the base, then add:
    - +12 if the candidate genre is one of the user's top genres
    - +6  if the candidate artist is one of the user's top artists
    - up to +25 from a log-scaled Last.fm playcount (popularity safety)

    Diversification: the top-N selection caps the number of picks per
    artist to avoid "20 tracks from the same artist".
    """
    top_genres = {name.lower() for name, _count in profile.top_genres}
    top_artists = {name.lower() for name, _score in profile.top_artists}
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
        if recs_store.is_active(candidate.isrc):
            counters["active"] += 1
            continue
        if is_duplicate(candidate.isrc, candidate.title, candidate.artist, tracks_store):
            counters["library"] += 1
            continue

        if candidate.track.genre and candidate.track.genre.lower() in top_genres:
            candidate.score += _GENRE_BONUS
        if candidate.artist and candidate.artist.lower() in top_artists:
            candidate.score += _ARTIST_BONUS
        if candidate.playcount > 0:
            # log10(1e7) ≈ 7 → 7 * 3.5 ≈ 24.5, capped at _PLAYCOUNT_LOG_BONUS_MAX.
            candidate.score += min(
                math.log10(candidate.playcount) * 3.5, _PLAYCOUNT_LOG_BONUS_MAX
            )

        kept.append(candidate)

    kept.sort(key=lambda item: item.score, reverse=True)
    return _diversify_by_artist(kept), counters


def _diversify_by_artist(
    candidates: list[RecommendationCandidate],
) -> list[RecommendationCandidate]:
    """Cap the number of tracks per artist while preserving the score order.

    Avoids the common failure mode "top 20 = 8 tracks from the same band".
    """
    per_artist: dict[str, int] = {}
    diversified: list[RecommendationCandidate] = []
    overflow: list[RecommendationCandidate] = []
    for candidate in candidates:
        key = candidate.artist.lower()
        if per_artist.get(key, 0) < _MAX_TRACKS_PER_ARTIST:
            diversified.append(candidate)
            per_artist[key] = per_artist.get(key, 0) + 1
        else:
            overflow.append(candidate)
    # If the diversified list is short (small library, niche genre), top it up
    # with the overflow so the user still gets a full batch.
    diversified.extend(overflow)
    return diversified
