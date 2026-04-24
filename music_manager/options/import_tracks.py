"""Import tracks from CSV — orchestrates §6 of the SPEC.

Loads CSV, checks for duplicates, resolves via Deezer, imports via
import_resolved_track(). Collects PendingTracks for unresolved rows.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from music_manager.core.config import Paths
from music_manager.core.io import load_csv, load_json, save_csv
from music_manager.core.logger import log_event
from music_manager.core.models import PendingTrack
from music_manager.pipeline.dedup import is_duplicate
from music_manager.pipeline.importer import cleanup_covers, import_resolved_track
from music_manager.services.albums import Albums
from music_manager.services.apple import add_to_playlist
from music_manager.services.resolver import resolve
from music_manager.services.tracks import Tracks

# ── Result type ──────────────────────────────────────────────────────────────


@dataclass
class ImportResult:
    """Summary of a CSV import run."""

    imported: int = 0
    skipped: int = 0
    failed: int = 0
    pending: list[PendingTrack] = field(default_factory=list)
    playlist_added: int = 0
    playlist_already: int = 0


# ── Entry point ──────────────────────────────────────────────────────────────


def process_csv(
    csv_path: str,
    paths: Paths,
    tracks_store: Tracks,
    albums_store: Albums,
    on_row: Callable[[int, int, str, str, str], None] | None = None,
) -> ImportResult:
    """Process a CSV file: dedup → resolve → import. Returns ImportResult.

    on_row(index, total, title, artist, status) is called after each row.
    Status: "skipped", "done", or PendingTrack.reason.
    """
    result = ImportResult()

    rows = load_csv(csv_path)
    if not rows:
        return result

    # ── Load ignored tracks ──────────────────────────────
    ignored_tracks: set[str] = set()
    prefs = load_json(paths.preferences_path)
    raw = prefs.get("ignored_tracks", [])
    if isinstance(raw, list):
        ignored_tracks = set(raw)

    # ── Detect playlist mode ─────────────────────────────
    is_playlist = os.path.dirname(os.path.abspath(csv_path)) == os.path.abspath(
        paths.playlists_dir
    )
    playlist_name = ""
    if is_playlist:
        playlist_name = os.path.splitext(os.path.basename(csv_path))[0]

    # ── Process each row ─────────────────────────────────
    total = len(rows)
    rows_to_keep: list[dict] = []
    playlist_ids: list[str] = []  # collect apple_ids for batch playlist add

    for idx, row in enumerate(rows):
        title = row.get("title", "")
        artist = row.get("artist", "")
        album = row.get("album", "")
        isrc = (row.get("isrc", "") or "").upper()

        # Ignored check
        if f"{title.lower()}::{artist.lower()}" in ignored_tracks:
            result.skipped += 1
            if on_row:
                on_row(idx, total, title, artist, "skipped")
            continue

        # Dedup check
        if is_duplicate(isrc, title, artist, tracks_store):
            result.skipped += 1
            if on_row:
                on_row(idx, total, title, artist, "skipped")
            # Playlist: collect apple_id for batch add later
            if playlist_name:
                apple_id = find_apple_id(isrc, title, artist, tracks_store)
                if apple_id:
                    playlist_ids.append(apple_id)
                else:
                    log_event(
                        "playlist_missing_apple_id",
                        title=title,
                        artist=artist,
                        reason="duplicate_but_no_apple_id",
                    )
            continue

        # Check for failed status → remove and retry
        remove_failed(isrc, title, artist, tracks_store)

        # Resolve via Deezer
        resolution = resolve(title, artist, album, isrc, albums_store)

        if resolution.status != "resolved" or resolution.track is None:
            pending = PendingTrack(
                reason=resolution.status,
                csv_title=title,
                csv_artist=artist,
                csv_album=album,
                track=resolution.track,
                album_mismatch=resolution.album_mismatch,
                candidates=resolution.candidates,
            )
            result.pending.append(pending)
            rows_to_keep.append(row)
            if on_row:
                on_row(idx, total, title, artist, resolution.status)
            continue

        # Import resolved track
        pending = import_resolved_track(
            resolution.track,
            paths,
            tracks_store,
            albums_store,
            csv_title=title,
            csv_artist=artist,
            csv_album=album,
        )

        if pending:
            result.pending.append(pending)
            rows_to_keep.append(row)
            if on_row:
                on_row(idx, total, title, artist, pending.reason)
        else:
            result.imported += 1
            if on_row:
                on_row(idx, total, title, artist, "done")
            # Playlist: collect apple_id
            if playlist_name and resolution.track.apple_id:
                playlist_ids.append(resolution.track.apple_id)

    # ── Playlist: batch add all tracks in one AppleScript call ──
    if playlist_name and playlist_ids:
        result.playlist_added = add_to_playlist(playlist_name, playlist_ids)
        result.playlist_already = len(playlist_ids) - result.playlist_added

    # ── Save stores (batched, not per-track — crash safety) ────────
    tracks_store.save()
    albums_store.save()

    # ── Cleanup cached covers ───────────────────────────
    cleanup_covers(paths.tmp_dir)

    # ── Save CSV (remove imported rows — playlists keep all rows) ─
    if not is_playlist:
        save_csv(csv_path, rows_to_keep)

    log_event(
        "import_csv",
        csv=csv_path,
        imported=result.imported,
        skipped=result.skipped,
        pending=len(result.pending),
    )

    return result


# ── Public helpers ───────────────────────────────────────────────────────────


def find_apple_id(isrc: str, title: str, artist: str, tracks_store: Tracks) -> str:
    """Find apple_id for a track already in the store.

    Same matching logic as is_duplicate(): ISRC → strict → soft fallback.
    """
    from music_manager.core.normalize import (  # noqa: PLC0415
        first_artist,
        normalize,
        prepare_title,
    )

    isrc = (isrc or "").upper()

    # By ISRC
    if isrc:
        entry = tracks_store.get_by_isrc(isrc)
        if entry and entry.get("apple_id"):
            return entry["apple_id"]

    # O(1) index lookup by normalized title+artist
    norm_title = normalize(title)
    norm_artist = normalize(artist)
    entry = tracks_store.get_by_title_artist(norm_title, norm_artist)
    if entry and entry.get("apple_id"):
        # If both have ISRC and they differ → different recordings, skip
        entry_isrc = (entry.get("isrc", "") or "").upper()
        if not (isrc and entry_isrc and isrc != entry_isrc):
            return entry["apple_id"]
        # ISRC conflict but same CSV origin → already processed
        csv_t = entry.get("csv_title") or ""
        if (
            csv_t
            and normalize(csv_t) == norm_title
            and normalize(entry.get("csv_artist") or "") == norm_artist
        ):
            return entry["apple_id"]

    # Soft fallback: linear scan for csv_title and prepare_title
    prep_title = prepare_title(title)
    first_norm_artist = normalize(first_artist(artist))

    for entry in tracks_store.all().values():
        if not entry.get("apple_id"):
            continue

        # Strict: csv_title + csv_artist (bypasses ISRC conflict — same CSV origin)
        csv_t = entry.get("csv_title", "")
        if (
            csv_t
            and normalize(csv_t) == norm_title
            and normalize(entry.get("csv_artist", "")) == norm_artist
        ):
            return entry["apple_id"]

        # Soft: prepare_title + first_artist
        # But not if both have ISRC and they differ (different recordings)
        entry_isrc = (entry.get("isrc", "") or "").upper()
        if isrc and entry_isrc and isrc != entry_isrc:
            continue
        if (
            prepare_title(entry.get("title", "")) == prep_title
            and normalize(first_artist(entry.get("artist", ""))) == first_norm_artist
        ):
            return entry["apple_id"]

    return ""


def remove_failed(
    isrc: str,
    title: str,
    artist: str,
    tracks_store: Tracks,
) -> None:
    """Remove failed entries from tracks.json so they can be retried."""
    from music_manager.core.normalize import normalize  # noqa: PLC0415

    if isrc:
        entry = tracks_store.get_by_isrc(isrc)
        if entry and entry.get("status") == "failed":
            # Find the apple_id key for this entry
            for aid, ent in list(tracks_store.all().items()):
                if ent is entry:
                    tracks_store.remove(aid)
                    break
            return

    norm_title = normalize(title)
    norm_artist = normalize(artist)
    for apple_id, entry in list(tracks_store.all().items()):
        if entry.get("status") != "failed":
            continue
        if (
            normalize(entry.get("title", "")) == norm_title
            and normalize(entry.get("artist", "")) == norm_artist
        ):
            tracks_store.remove(apple_id)
            return
