"""Identify library — link Apple Music tracks to Deezer (§3).

Strategy:
1. Scan ISRC from files (mutagen, incremental)
2. Match from known albums (0 API — cached tracklists)
3. Group remaining by album → UI picks Deezer album → batch confirm
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from music_manager.core.normalize import match_score, normalize, prepare_title
from music_manager.services.albums import Albums
from music_manager.services.tagger import scan_isrc, write_isrc
from music_manager.services.tracks import Tracks

# ── Result types ─────────────────────────────────────────────────────────


@dataclass
class IdentifyResult:
    """Summary of an identification run."""

    isrc_from_files: int = 0
    auto_validated: int = 0
    albums_to_review: list[dict] = field(default_factory=list)


# ── Entry point ──────────────────────────────────────────────────────────


def identify_library(
    tracks_store: Tracks,
    albums_store: Albums,
    on_progress: Callable[[int, int], None] | None = None,
    preferences_path: str = "",
) -> IdentifyResult:
    """Identify unlinked tracks. No per-track API calls.

    Phase 1: scan mutagen for ISRCs (incremental, 0 API)
    Phase 2: match from known albums via cached tracklists (0 API)
    Phase 3: group remaining by album for UI review
    """
    import time  # noqa: PLC0415

    from music_manager.core.io import load_json  # noqa: PLC0415
    from music_manager.core.logger import log_event  # noqa: PLC0415

    result = IdentifyResult()

    # Load ignored tracks
    ignored_tracks: set[str] = set()
    if preferences_path:
        prefs = load_json(preferences_path)
        raw = prefs.get("ignored_tracks", [])
        if isinstance(raw, list):
            ignored_tracks = set(raw)

    # ── Phase 1: scan mutagen (incremental) ──────────────
    t0 = time.perf_counter()
    _scan_isrc_phase(tracks_store, result)
    log_event(
        "identify_phase1",
        duration_ms=int((time.perf_counter() - t0) * 1000),
        isrc_found=result.isrc_from_files,
    )

    # ── Phase 2: match from known albums (0 API) ────────
    unidentified = [
        (apple_id, entry)
        for apple_id, entry in tracks_store.all().items()
        if not entry.get("deezer_id")
        and f"{entry.get('title') or ''}::{entry.get('artist') or ''}" not in ignored_tracks
    ]

    if on_progress:
        on_progress(0, len(unidentified))

    t1 = time.perf_counter()
    remaining = _match_known_albums(
        unidentified,
        tracks_store,
        albums_store,
        result,
        on_progress,
    )
    log_event(
        "identify_phase2",
        duration_ms=int((time.perf_counter() - t1) * 1000),
        matched=result.auto_validated,
        remaining=len(remaining),
    )

    # ── Phase 3: group remaining by album ────────────────
    result.albums_to_review = _group_by_album(remaining)
    log_event("identify_phase3", albums_to_review=len(result.albums_to_review))

    tracks_store.save()
    albums_store.save()
    return result


# ── Album confirmation ───────────────────────────────────────────────────


def confirm_album(
    album_id: int,
    apple_ids: list[str],
    tracks_store: Tracks,
    albums_store: Albums,
) -> tuple[int, list[str]]:
    """Confirm album identification — match all tracks via tracklist.

    Returns (matched_count, unmatched_apple_ids).
    """
    from music_manager.services.resolver import (  # noqa: PLC0415
        fetch_album_with_cover,
        get_album_tracklist,
    )

    tracklist = get_album_tracklist(album_id, albums_store)
    if not tracklist:
        albums_store.save()
        return 0, list(apple_ids)

    fetch_album_with_cover(album_id, albums_store)  # cache album data

    matched, unmatched = _match_tracks_in_tracklist(
        apple_ids,
        tracklist,
        tracks_store,
        albums_store,
    )

    tracks_store.save()
    albums_store.save()
    return matched, unmatched


def confirm_track(
    apple_id: str,
    deezer_track: dict,
    tracks_store: Tracks,
    albums_store: Albums | None = None,
    file_path: str = "",
) -> None:
    """Confirm a manual identification choice for a single track."""
    deezer_id = deezer_track.get("deezer_id") or deezer_track.get("id", 0)

    update_data: dict = {
        "deezer_id": deezer_id,
        "album_id": deezer_track.get("album_id", 0),
        "isrc": deezer_track.get("isrc", ""),
        "cover_url": deezer_track.get("cover_url", ""),
    }

    if deezer_id and albums_store:
        from music_manager.services.resolver import resolve_by_id  # noqa: PLC0415

        full_track = resolve_by_id(deezer_id, albums_store)
        if full_track:
            update_data = _track_to_update_dict(full_track)

    tracks_store.update(apple_id, update_data)
    isrc = update_data.get("isrc", "")
    if file_path and isrc:
        write_isrc(file_path, isrc)
    tracks_store.save()
    if albums_store:
        albums_store.save()


# ── Private Functions ────────────────────────────────────────────────────


def _scan_isrc_phase(tracks_store: Tracks, result: IdentifyResult) -> None:
    """Phase 1: scan ISRCs from audio files (incremental)."""
    entries_without_isrc = tracks_store.without_isrc()
    if not entries_without_isrc:
        return

    from music_manager.core.models import LibraryEntry  # noqa: PLC0415

    scan_entries = {}
    for apple_id, entry in entries_without_isrc:
        scan_entries[apple_id] = LibraryEntry(
            apple_id=apple_id,
            title=entry.get("title", ""),
            artist=entry.get("artist", ""),
            album=entry.get("album", ""),
            file_path=entry.get("file_path", ""),
        )

    result.isrc_from_files = scan_isrc(scan_entries)

    for apple_id, lib_entry in scan_entries.items():
        if lib_entry.isrc:
            tracks_store.update(apple_id, {"isrc": lib_entry.isrc})


def _match_known_albums(
    unidentified: list[tuple[str, dict]],
    tracks_store: Tracks,
    albums_store: Albums,
    result: IdentifyResult,
    on_progress: Callable[[int, int], None] | None,
) -> list[tuple[str, dict]]:
    """Phase 2: match tracks in known album tracklists (0 API calls)."""
    from music_manager.core.logger import log_event  # noqa: PLC0415
    from music_manager.services.resolver import (  # noqa: PLC0415
        get_album_tracklist,
        resolve_by_id,
    )

    # Build known album lookup
    known_albums: dict[str, int] = {}
    for entry in tracks_store.all().values():
        alb = entry.get("album") or ""
        aid = entry.get("album_id")
        if alb and aid:
            known_albums[normalize(alb)] = aid

    # Cache tracklists per album_id (avoid repeated lookups)
    tracklist_cache: dict[int, list[dict]] = {}

    remaining: list[tuple[str, dict]] = []
    total = len(unidentified)

    for idx, (apple_id, entry) in enumerate(unidentified):
        if on_progress:
            on_progress(idx + 1, total)

        title = entry.get("title") or ""
        artist = entry.get("artist") or ""
        album = entry.get("album") or ""
        album_id = known_albums.get(normalize(album), 0) if album else 0
        if not album_id:
            log_event(
                "identify_phase2_miss",
                title=title, artist=artist, album=album,
                reason="unknown_album",
            )
            remaining.append((apple_id, entry))
            continue

        # Get cached tracklist
        if album_id not in tracklist_cache:
            tracklist_cache[album_id] = (
                get_album_tracklist(
                    album_id,
                    albums_store,
                )
                or []
            )

        tracklist = tracklist_cache[album_id]
        if not tracklist:
            log_event(
                "identify_phase2_miss",
                title=title, artist=artist, album=album,
                reason="empty_tracklist", album_id=album_id,
            )
            remaining.append((apple_id, entry))
            continue

        # Match by title
        dz_match = _find_in_tracklist(title, tracklist)
        if not dz_match:
            dz_titles = [t.get("title", "") for t in tracklist[:5]]
            log_event(
                "identify_phase2_miss",
                title=title, artist=artist, album=album,
                reason="title_not_in_tracklist", album_id=album_id,
                tracklist_size=len(tracklist), sample_titles=dz_titles,
            )
            remaining.append((apple_id, entry))
            continue

        dz_id = dz_match.get("id", 0)
        full = resolve_by_id(dz_id, albums_store) if dz_id else None
        if full:
            store_track_data(apple_id, full, entry, tracks_store)
            result.auto_validated += 1
        else:
            log_event(
                "identify_phase2_miss",
                title=title, artist=artist, album=album,
                reason="resolve_by_id_failed", dz_id=dz_id,
            )
            remaining.append((apple_id, entry))

    return remaining


def _match_tracks_in_tracklist(
    apple_ids: list[str],
    tracklist: list[dict],
    tracks_store: Tracks,
    albums_store: Albums,
) -> tuple[int, list[str]]:
    """Match apple_ids against a Deezer tracklist. Returns (matched, unmatched)."""
    from music_manager.services.resolver import resolve_by_id  # noqa: PLC0415

    matched = 0
    unmatched: list[str] = []

    for apple_id in apple_ids:
        entry = tracks_store.all().get(apple_id)
        if not entry:
            continue

        title = entry.get("title") or ""
        dz_match = _find_in_tracklist(title, tracklist)
        if not dz_match:
            unmatched.append(apple_id)
            continue

        dz_id = dz_match.get("id", 0)
        full = resolve_by_id(dz_id, albums_store) if dz_id else None
        if full:
            store_track_data(apple_id, full, entry, tracks_store)
            matched += 1
        else:
            unmatched.append(apple_id)

    return matched, unmatched


def _find_in_tracklist(title: str, tracklist: list[dict]) -> dict | None:
    """Find a track in a tracklist by normalize, prepare_title, then fuzzy.

    Three passes (strictest first):
    1. Exact normalized match
    2. prepare_title match (strips parens)
    3. Fuzzy fallback (match_score >= 85)
    """
    norm_t = normalize(title)
    prep_t = prepare_title(title)

    # Pass 1: exact normalized
    for dz in tracklist:
        if normalize(dz.get("title", "")) == norm_t:
            return dz

    # Pass 2: prepare_title (strips parens)
    for dz in tracklist:
        if prepare_title(dz.get("title", "")) == prep_t:
            return dz

    # Pass 3: fuzzy fallback
    best_score = 0.0
    best_match = None
    for dz in tracklist:
        score = match_score(title, dz.get("title", ""), "title")
        if score >= 85.0 and score > best_score:
            best_score = score
            best_match = dz
    return best_match


def store_track_data(
    apple_id: str,
    track: object,
    entry: dict,
    tracks_store: Tracks,
) -> None:
    """Store all Deezer data from a resolved Track into tracks_store."""
    update_data = _track_to_update_dict(track)
    tracks_store.update(apple_id, update_data)

    file_path = entry.get("file_path") or ""
    isrc = update_data.get("isrc", "")
    if file_path and isrc:
        write_isrc(file_path, isrc)


def _track_to_update_dict(track: object) -> dict:
    """Convert a Track object to a dict for tracks_store.update()."""
    from music_manager.core.models import Track  # noqa: PLC0415

    if isinstance(track, Track):
        return {
            key: val
            for key, val in track.to_dict().items()
            if key
            in {
                "deezer_id",
                "album_id",
                "isrc",
                "cover_url",
                "genre",
                "release_date",
                "track_number",
                "total_tracks",
                "disk_number",
                "total_discs",
                "album_artist",
                "duration",
                "preview_url",
            }
        }
    return {}


def _group_by_album(
    unresolved: list[tuple[str, dict]],
) -> list[dict]:
    """Group unresolved tracks by normalized album name."""
    groups: dict[str, dict] = {}
    for apple_id, entry in unresolved:
        album = entry.get("album") or ""
        artist = entry.get("artist") or ""
        title = entry.get("title") or ""
        key = normalize(album) if album else f"__single_{apple_id}"

        if key not in groups:
            groups[key] = {
                "album_name": album,
                "artist": artist,
                "apple_ids": [],
                "titles": [],
            }
        groups[key]["apple_ids"].append(apple_id)
        groups[key]["titles"].append(title)

    return list(groups.values())
