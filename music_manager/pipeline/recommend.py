"""Recommendations pipeline — adaptive learning over Last.fm + Deezer.

End-to-end orchestration of one generation run targeted at one sub-playlist
of the ``for me`` Apple Music folder. Modes: ``library`` (whole taste),
``playlist:<name>`` (seed from one user playlist), ``genre:<name>``,
``mood:<tag>``, ``discovery`` (sortir des sentiers battus).

Pipeline phases:

A. ``_detect_deltas`` — diff ``last_seen_loved`` / ``last_seen_playcount``
   snapshots against the current Apple state; emit ``loved_delta`` /
   ``playcount_delta`` signals (positive reinforcement after the user has
   actually listened to a previous reco).
B. ``scan_outcomes`` — classify each active reco of the target playlist:
   ``adopted_playlist`` (moved to another user playlist), ``kept_library``
   (still in library but no other playlist), ``rejected`` (gone). Emits
   one signal per outcome, persists in ``RecommendationsStore.outcomes``.
C. ``build_profile`` — score the user's taste over ``tracks.json``,
   optionally restricted to a seed playlist's apple_ids.
D. ``_collect_lastfm_candidates`` — query Last.fm (track.getSimilar for
   seed-based modes, tag.getTopTracks for mood; chart.getTopTracks as
   discovery cold-start fallback; artist.getSimilar to widen).
E. ``_resolve_candidates`` — Deezer search per candidate (parallel).
F. ``_dedup_and_rank`` — drop blacklist/active/library duplicates, then
   apply scoring boosts: base Last.fm match × 100, +12 genre / +6 artist
   from profile, log-scaled playcount, ±15 / ±20 from learned affinity
   over 180-day window, ±10 / +20 in discovery mode.
G. ``import_resolved_track`` — full M4A pipeline (Deezer → YouTube →
   Apple Music). Reused as-is.
H. ``apple.add_to_playlist_in_folder`` — sync the imported tracks into
   ``for me/<playlist>``, creating the folder + playlist if missing.
I. Persist: ``signals.jsonl`` audit event + stores save + record_generation.

All errors are caught and reported via :class:`GenerationResult` — a
single failure never aborts the run.
"""

import math
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from music_manager.core.config import Paths
from music_manager.core.logger import log_event
from music_manager.core.models import Track
from music_manager.core.profile import Profile, build_profile
from music_manager.pipeline.dedup import is_duplicate
from music_manager.pipeline.importer import cleanup_covers, import_resolved_track
from music_manager.services import apple, lastfm
from music_manager.services.albums import Albums
from music_manager.services.recommendations_store import RecommendationsStore
from music_manager.services.resolver import build_track, fetch_album_with_cover, search_track
from music_manager.services.signals import SignalsLog
from music_manager.services.tracks import Tracks

# ── Constants ────────────────────────────────────────────────────────────────

RECO_FOLDER_NAME = apple.RECO_FOLDER_NAME
DEFAULT_TARGET = 20

_PLAYLIST_NAME_MAX = 50
_KNOWN_MODE_PREFIXES = ("genre", "playlist", "mood")

_SEED_TRACK_COUNT = 50
_SEED_FALLBACK_TRACK_COUNT = 50
_MIN_CANDIDATE_POOL = 50
_DEEZER_RESOLVE_WORKERS = 8
_TOP_ARTIST_FALLBACK_LIMIT = 5
_GENRE_BONUS = 12.0
_ARTIST_BONUS = 6.0
_LOCAL_ARTIST_PLAYCOUNT_BONUS_MAX = 18.0
_RECENT_RELEASE_BONUS_MAX = 18.0
_RECENT_RELEASE_DAYS = 365

# Quality filters / boosts
_MIN_LASTFM_MATCH = 0.30
_PLAYCOUNT_LOG_BONUS_MAX = 25.0  # bonus capped at +25 for ~10M playcount
_MAX_TRACKS_PER_ARTIST = 2

# Negative reinforcement: skip seeds whose past picks were mostly blacklisted.
_SEED_BLACKLIST_RATIO_THRESHOLD = 0.5

# Adaptive affinity scoring (Étape 7) — learned from signals.jsonl outcomes
# over the default 180-day window. Bonuses fire when an artist/genre has
# proven user resonance; maluses fire when it has been repeatedly rejected.
_AFFINITY_ARTIST_BONUS = 15.0
_AFFINITY_ARTIST_MALUS = 20.0
_AFFINITY_GENRE_BONUS = 10.0
_AFFINITY_GENRE_MALUS = 15.0
_AFFINITY_ARTIST_POS_THRESHOLD = 0.5
_AFFINITY_ARTIST_NEG_THRESHOLD = -0.3
_AFFINITY_GENRE_POS_THRESHOLD = 0.5
_AFFINITY_GENRE_NEG_THRESHOLD = -0.3

# Discovery mode tuning (Étape 9) — biases away from the user's comfort
# zone: narrower Last.fm match band (drops obvious picks above 0.7 and
# noise below 0.4), penalty on already-known artists, bonus on artists
# the user has never had in their library.
_DISCOVERY_LASTFM_MATCH_MIN = 0.4
_DISCOVERY_LASTFM_MATCH_MAX = 0.7
_DISCOVERY_FAMILIARITY_MALUS = 10.0
_DISCOVERY_COLD_ARTIST_BONUS = 20.0


# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class RecommendationCandidate:
    """A Last.fm candidate already resolved on Deezer (ready to import)."""

    isrc: str
    deezer_id: int
    title: str
    artist: str
    track: Track
    # "lastfm_similar" | "lastfm_tag" | "lastfm_artist_similar" | "lastfm_chart"
    source: str
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
    rejected: int = 0
    adopted_playlist: int = 0
    kept_library: int = 0
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
    signals: SignalsLog | None = None,
    target_count: int = DEFAULT_TARGET,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> GenerationResult:
    """Generate and import recommendations into ``for me/<playlist>``.

    Accepted modes: ``library``, ``general`` (legacy alias of ``library``),
    ``genre:<name>``, ``playlist:<name>``, ``mood:<tag>``, ``discovery``.

    ``signals`` is the event log driving adaptive learning. If omitted,
    one is opened lazily at ``paths.signals_log_path``.

    ``on_progress(phase, current, total)`` is called between phases:
    ``scan`` (after outcomes classification), ``candidates`` (per Last.fm
    batch), ``resolve`` (per Deezer search), ``import`` (per track), and
    ``done``.
    """
    result = GenerationResult()

    if not lastfm.get_api_key():
        result.error = "lastfm_no_api_key"
        log_event("recommend_no_api_key", mode=mode)
        return result

    try:
        playlist_name = playlist_name_for_mode(mode)
    except ValueError as exc:
        result.error = "invalid_mode"
        log_event("recommend_invalid_mode", mode=mode, error=str(exc))
        return result

    sig = signals if signals is not None else SignalsLog(paths.signals_log_path)

    # A. Detect loved/play_count deltas BEFORE any outcome classification.
    _detect_deltas(recs_store, tracks_store, sig)

    # B. Classify outcomes for this playlist (adopted/kept/rejected).
    outcomes_counts = scan_outcomes(recs_store, sig, playlist_name=playlist_name)
    result.rejected = outcomes_counts["rejected"]
    result.adopted_playlist = outcomes_counts["adopted_playlist"]
    result.kept_library = outcomes_counts["kept_library"]
    if on_progress:
        total_outcomes = sum(outcomes_counts.values())
        on_progress("scan", total_outcomes, total_outcomes)

    # C. Build profile, optionally restricted to a seed playlist.
    playlist_apple_ids: set[str] | None = None
    if mode.startswith("playlist:"):
        seed_playlist_name = mode.split(":", 1)[1].strip()
        try:
            playlist_apple_ids = set(apple.get_playlist_tracks(seed_playlist_name))
        except Exception:  # noqa: BLE001
            playlist_apple_ids = set()
    profile = build_profile(
        tracks_store.all(), mode=mode, playlist_apple_ids=playlist_apple_ids
    )

    # D. Candidates (with negative reinforcement on bad seeds).
    seed_blacklist_ratio = recs_store.seed_quality()
    seeds = _collect_lastfm_candidates(profile, mode, seed_blacklist_ratio, on_progress)
    if not seeds:
        result.error = result.error or "lastfm_empty"
        log_event("recommend_no_candidates", mode=mode)
        return result
    result.candidates_total = len(seeds)

    # E. Resolve on Deezer (parallel).
    resolved = _resolve_candidates(seeds, albums_store, on_progress)

    # F. Dedup + rank (affinity + discovery boosts).
    kept, counters = _dedup_and_rank(
        resolved, profile, tracks_store, recs_store, signals=sig, mode=mode
    )
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
            candidate.track, paths, tracks_store, albums_store,
        )
        if pending is None and candidate.track.apple_id:
            apple_ids.append(candidate.track.apple_id)
            outcome = recs_store.add_active(
                {
                    "isrc": candidate.isrc,
                    "apple_id": candidate.track.apple_id,
                    "title": candidate.title,
                    "artist": candidate.artist,
                    "genre": candidate.track.genre or "",
                    "source": candidate.source,
                    "seed_isrc": candidate.seed_isrc,
                    "score": candidate.score,
                    "mode": mode,
                    "playlist": playlist_name,
                }
            )
            if not outcome.get("added") and outcome.get("reason") == "duplicate":
                existing_playlist = outcome.get("current_playlist") or ""
                if existing_playlist and existing_playlist != playlist_name:
                    log_event(
                        "recommend_active_cross_playlist",
                        isrc=candidate.isrc,
                        attempted_playlist=playlist_name,
                        current_playlist=existing_playlist,
                    )
            sig.log(
                "recommend_imported",
                isrc=candidate.isrc,
                apple_id=candidate.track.apple_id,
                playlist=playlist_name,
                mode=mode,
                seed_isrc=candidate.seed_isrc,
                source=candidate.source,
                score=candidate.score,
                title=candidate.title,
                artist=candidate.artist,
                genre=candidate.track.genre or "",
            )
            result.imported += 1
            result.imported_isrcs.append(candidate.isrc)
            # Crash-safe: persist after each success so a network drop
            # mid-run doesn't lose the mapping the next scan_outcomes needs.
            recs_store.save()
        else:
            result.failed += 1
        if on_progress:
            on_progress("import", idx, len(top))

    # Cleanup downloaded cover files now that all imports finished
    # (each cover_<album_id>.jpg in tmp_dir is ~150KB — accumulates otherwise).
    cleanup_covers(paths.tmp_dir)

    # H. Playlist sync — into the ``for me`` folder. We warn loudly when a
    # user playlist already bears the folder name (creating the folder would
    # leave two ``for me`` items side by side). We still proceed so the user
    # at least gets the new recos appended somewhere — the warning surfaces
    # in logs.jsonl and the UI summary.
    if apple_ids:
        try:
            if apple.user_playlist_collides_with_folder(RECO_FOLDER_NAME):
                log_event(
                    "recommend_folder_name_collision",
                    folder=RECO_FOLDER_NAME,
                    hint="rename the existing user playlist to avoid duplication",
                )
            added_count = apple.add_to_playlist_in_folder(
                RECO_FOLDER_NAME, playlist_name, apple_ids
            )
            # add_to_playlist_in_folder swallows AppleScript errors and
            # returns 0; surface that here so the user knows the recs
            # are in recommendations.json but not visible in Apple Music.
            if added_count == 0:
                log_event(
                    "recommend_playlist_sync_silent",
                    folder=RECO_FOLDER_NAME,
                    playlist=playlist_name,
                    expected=len(apple_ids),
                )
        except Exception as exc:  # noqa: BLE001
            log_event(
                "recommend_playlist_failed",
                error=str(exc),
                folder=RECO_FOLDER_NAME,
                playlist=playlist_name,
            )

    # I. Final save + audit event.
    sig.log(
        "generation_run",
        mode=mode,
        playlist=playlist_name,
        imported=result.imported,
        failed=result.failed,
        adopted_playlist=outcomes_counts["adopted_playlist"],
        kept_library=outcomes_counts["kept_library"],
        rejected=outcomes_counts["rejected"],
    )
    recs_store.record_generation()
    recs_store.save()
    tracks_store.save()
    albums_store.save()

    log_event(
        "recommend_done",
        mode=mode,
        imported=result.imported,
        failed=result.failed,
        adopted_playlist=outcomes_counts["adopted_playlist"],
        kept_library=outcomes_counts["kept_library"],
        rejected=outcomes_counts["rejected"],
    )

    if on_progress:
        on_progress("done", result.imported, target_count)

    return result


def scan_outcomes(
    recs_store: RecommendationsStore,
    signals: SignalsLog,
    *,
    playlist_name: str,
    folder_name: str = RECO_FOLDER_NAME,
) -> dict[str, int]:
    """Classify outcomes for the recos sitting in ``folder_name/playlist_name``.

    For each active entry whose ``playlist`` field matches
    ``playlist_name`` and which is no longer in the live Apple playlist:

    - If still in another user playlist (outside ``folder_name``)
      → ``adopted_playlist``
    - If still in the library but in no other user playlist
      → ``kept_library``
    - If gone from the library
      → ``rejected``

    Logs one signal per classified outcome and records it in
    ``recs_store``. Returns ``{adopted_playlist: N, kept_library: N,
    rejected: N}``.

    Safety: if the playlist itself is absent from the folder, returns
    zeros without mass-classifying (ambiguous user state).
    """
    counts = {"adopted_playlist": 0, "kept_library": 0, "rejected": 0}

    target = [
        (isrc, entry)
        for isrc, entry in recs_store.all_active().items()
        if entry.get("playlist") == playlist_name
    ]
    if not target:
        return counts

    try:
        current = set(apple.get_playlist_tracks_in_folder(folder_name, playlist_name))
    except Exception as exc:  # noqa: BLE001
        log_event(
            "recommend_scan_failed",
            error=str(exc),
            folder=folder_name,
            playlist=playlist_name,
        )
        return counts

    if not current and not apple.playlist_exists_in_folder(folder_name, playlist_name):
        log_event(
            "recommend_playlist_missing", folder=folder_name, playlist=playlist_name
        )
        return counts

    missing_entries = [
        (isrc, entry)
        for isrc, entry in target
        if entry.get("apple_id") and entry["apple_id"] not in current
    ]
    if not missing_entries:
        return counts

    missing_apple_ids = [entry["apple_id"] for _, entry in missing_entries]
    try:
        still_exist = apple.apple_ids_exist(missing_apple_ids)
    except Exception as exc:  # noqa: BLE001
        log_event("recommend_scan_failed", error=str(exc))
        return counts

    for isrc, entry in missing_entries:
        apple_id = entry["apple_id"]
        title = entry.get("title", "")
        artist = entry.get("artist", "")
        genre = entry.get("genre", "")

        if apple_id not in still_exist:
            signals.log(
                "recommend_rejected",
                isrc=isrc,
                apple_id=apple_id,
                from_playlist=playlist_name,
                title=title,
                artist=artist,
                genre=genre,
            )
            recs_store.record_outcome(
                isrc,
                state="rejected",
                from_playlist=playlist_name,
                title=title,
                artist=artist,
                genre=genre,
            )
            counts["rejected"] += 1
            continue

        try:
            detailed = apple.get_playlist_membership_detailed(apple_id)
        except Exception:  # noqa: BLE001
            detailed = []

        # Adoption = any membership outside the source playlist, including
        # moves to another ``for me / <other>`` sub-playlist (the user is
        # explicitly re-categorising the reco, that's a positive signal).
        other_playlists = [
            name
            for name, parent, _ids in detailed
            if not (parent == folder_name and name == playlist_name)
        ]

        if other_playlists:
            signals.log(
                "recommend_adopted_playlist",
                isrc=isrc,
                apple_id=apple_id,
                from_playlist=playlist_name,
                to_playlists=other_playlists,
                title=title,
                artist=artist,
                genre=genre,
            )
            recs_store.record_outcome(
                isrc,
                state="adopted_playlist",
                from_playlist=playlist_name,
                to_playlists=other_playlists,
                title=title,
                artist=artist,
                genre=genre,
            )
            counts["adopted_playlist"] += 1
        else:
            signals.log(
                "recommend_kept_library",
                isrc=isrc,
                apple_id=apple_id,
                from_playlist=playlist_name,
                title=title,
                artist=artist,
                genre=genre,
            )
            recs_store.record_outcome(
                isrc,
                state="kept_library",
                from_playlist=playlist_name,
                title=title,
                artist=artist,
                genre=genre,
            )
            counts["kept_library"] += 1

    log_event(
        "recommend_scan_outcomes",
        playlist=playlist_name,
        adopted_playlist=counts["adopted_playlist"],
        kept_library=counts["kept_library"],
        rejected=counts["rejected"],
    )
    return counts


# ── Private Functions ────────────────────────────────────────────────────────


def _detect_deltas(
    recs_store: RecommendationsStore,
    tracks_store: Tracks,
    signals: SignalsLog,
) -> dict[str, int]:
    """Compare per-active ``last_seen_*`` snapshots vs current Apple state.

    For each active recommendation:
    - ``loved`` changed → log ``loved_delta`` with the new value
    - ``play_count`` increased → log ``playcount_delta`` with the delta
    - Snapshot updated either way

    Returns ``{loved: N, playcount: N}``.
    """
    counts = {"loved": 0, "playcount": 0}
    for isrc, active in recs_store.all_active().items():
        apple_id = active.get("apple_id") or ""
        if not apple_id:
            continue
        track = tracks_store.get_by_apple_id(apple_id)
        if not track:
            continue

        title = active.get("title", "")
        artist = active.get("artist", "")
        genre = active.get("genre", "")

        last_loved = bool(active.get("last_seen_loved", False))
        current_loved = bool(track.get("loved", False))
        loved_changed = last_loved != current_loved
        if loved_changed:
            signals.log(
                "loved_delta",
                isrc=isrc,
                apple_id=apple_id,
                to_loved=current_loved,
                title=title,
                artist=artist,
                genre=genre,
            )
            counts["loved"] += 1

        last_count = int(active.get("last_seen_playcount", 0) or 0)
        try:
            current_count = int(track.get("play_count") or 0)
        except (TypeError, ValueError):
            current_count = 0
        delta = current_count - last_count
        if delta > 0:
            signals.log(
                "playcount_delta",
                isrc=isrc,
                apple_id=apple_id,
                delta=delta,
                new_count=current_count,
                title=title,
                artist=artist,
                genre=genre,
            )
            counts["playcount"] += 1

        if loved_changed or current_count != last_count:
            recs_store.update_snapshot(
                isrc, loved=current_loved, playcount=current_count
            )
    return counts


def playlist_name_for_mode(mode: str) -> str:
    """Return the Apple Music sub-playlist name (inside the ``for me`` folder).

    Mapping:
    - ``library`` (or legacy ``general``) → ``"library"``
    - ``discovery`` → ``"discovery"``
    - ``genre:<value>`` → sanitized ``<value>``
    - ``playlist:<value>`` → sanitized ``<value>``
    - ``mood:<value>`` → sanitized ``<value>``

    Raises ``ValueError`` for unknown modes or empty/whitespace-only
    values after the prefix.
    """
    if not mode:
        raise ValueError(f"unknown mode: {mode!r}")
    if mode in ("library", "general"):
        return "library"
    if mode == "discovery":
        return "discovery"
    if ":" not in mode:
        raise ValueError(f"unknown mode: {mode!r}")
    prefix, value = mode.split(":", 1)
    if prefix not in _KNOWN_MODE_PREFIXES:
        raise ValueError(f"unknown mode prefix: {prefix!r}")
    sanitized = _sanitize_playlist_segment(value)
    if not sanitized:
        raise ValueError(f"empty {prefix} value: {value!r}")
    return sanitized


def _sanitize_playlist_segment(value: str) -> str:
    """Strip accents, lowercase, replace whitespace/slashes/quotes by ``-``."""
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
        # other chars dropped silently
    collapsed = "".join(cleaned)
    while "--" in collapsed:
        collapsed = collapsed.replace("--", "-")
    collapsed = collapsed.strip("-")
    return collapsed[:_PLAYLIST_NAME_MAX]


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
                mode=mode,
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
                mode=mode,
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
                    mode=mode,
                )

    # Discovery cold-start fallback: empty profile + empty pool → use the
    # Last.fm global chart so the user still gets something to explore.
    if mode == "discovery" and not candidates:
        for item in lastfm.get_chart_top_tracks(limit=200):
            _append_unique(
                candidates,
                seen,
                item,
                source="lastfm_chart",
                seed_isrc="",
                mode=mode,
            )

    return candidates


def _append_unique(
    candidates: list[dict[str, Any]],
    seen: set[tuple[str, str]],
    item: dict[str, Any],
    *,
    source: str,
    seed_isrc: str,
    mode: str = "library",
) -> None:
    name = (item.get("name") or "").strip()
    artist = (item.get("artist") or "").strip()
    if not artist:
        return
    match = float(item.get("match") or 0.0)
    if source == "lastfm_similar":
        if mode == "discovery":
            # Narrow band: drop too-noisy (< 0.4) AND too-obvious (> 0.7).
            if match < _DISCOVERY_LASTFM_MATCH_MIN or match > _DISCOVERY_LASTFM_MATCH_MAX:
                return
        elif match < _MIN_LASTFM_MATCH:
            # Default: drop low-confidence picks — < 0.30 is mostly noise.
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
    signals: SignalsLog | None = None,
    mode: str = "library",
) -> tuple[list[RecommendationCandidate], dict[str, int]]:
    """Apply dedup short-circuit, score boosts, then diversify by artist.

    Scoring on top of the Last.fm match base (``match * 100``):
    - +12 if the candidate genre is one of the user's top genres
    - +6  if the candidate artist is one of the user's top artists
    - up to +18 for artists the user actually plays a lot locally
    - up to +18 for recent releases (linear decay over one year)
    - up to +25 from a log-scaled Last.fm playcount (popularity safety)

    When ``signals`` is provided and adaptive learning has enough history
    (``signals.artist_affinity()`` / ``genre_affinity()`` over the default
    180-day window):
    - +15 / -20 from artist affinity (thresholds 0.5 / -0.3)
    - +10 / -15 from genre affinity  (thresholds 0.5 / -0.3)

    Diversification caps the number of tracks per artist to avoid the
    common "top 20 = 8 tracks from the same band" failure mode.
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
            str(entry.get("artist") or "").lower()
            for entry in tracks_store.all().values()
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
        _apply_local_artist_playcount_bonus(candidate, local_artist_playcounts)
        _apply_recent_release_bonus(candidate)
        if candidate.playcount > 0:
            # log10(1e7) ≈ 7 → 7 * 3.5 ≈ 24.5, capped at _PLAYCOUNT_LOG_BONUS_MAX.
            candidate.score += min(
                math.log10(candidate.playcount) * 3.5, _PLAYCOUNT_LOG_BONUS_MAX
            )

        _apply_affinity(candidate, artist_affinity, genre_affinity)

        if is_discovery:
            _apply_discovery_bonuses(candidate, top_artists, known_artists)

        kept.append(candidate)

    kept.sort(key=lambda item: item.score, reverse=True)
    return _diversify_by_artist(kept), counters


def _local_artist_playcounts(tracks_store: Tracks) -> dict[str, int]:
    """Aggregate Apple Music play_count by artist from the local library."""
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
    """Boost candidates by artists the user repeatedly plays locally."""
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
    """Boost newer releases so recommendations do not overfit old favorites."""
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
    """Parse Deezer album release dates without raising."""
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
    """Bias the candidate toward novelty.

    - Penalize artists the user already knows well (top_artists).
    - Reward artists never seen in the library (cold artist).
    """
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
    """Bump or dock the candidate's score based on learned affinities."""
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
