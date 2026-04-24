"""Import pipeline — centralized logic for importing a resolved track.

Used by §6 Import, §7 Review, §8 Complete Albums, §11 Modify Track.
"""

import os
import time
from datetime import datetime

from music_manager.core.config import Paths
from music_manager.core.logger import log_event
from music_manager.core.models import PendingTrack, Track
from music_manager.services.albums import Albums
from music_manager.services.apple import import_file
from music_manager.services.tagger import tag_audio_file
from music_manager.services.tracks import Tracks
from music_manager.services.youtube import download_track, search_by_isrc

# ── Constants ────────────────────────────────────────────────────────────────

_DURATION_RATIO_MIN = 0.93
_DURATION_RATIO_MAX = 1.07


# ── Entry point ──────────────────────────────────────────────────────────────


def import_resolved_track(
    track: Track,
    paths: Paths,
    tracks_store: Tracks,
    albums_store: Albums,
    csv_title: str = "",
    csv_artist: str = "",
    csv_album: str = "",
) -> PendingTrack | None:
    """Import a resolved track: cover → YouTube → duration → tag → Apple Music.

    Returns None on success, PendingTrack on failure or user decision needed.
    """
    label_title = csv_title or track.title
    label_artist = csv_artist or track.artist
    label_album = csv_album or track.album

    # ── Cover ────────────────────────────────────────────
    cover_path = download_cover(track, paths, albums_store)

    # ── YouTube ──────────────────────────────────────────
    candidates = search_by_isrc(track.isrc)
    if not candidates:
        return PendingTrack(
            reason="youtube_failed",
            csv_title=label_title,
            csv_artist=label_artist,
            csv_album=label_album,
            track=track,
        )

    best = candidates[0]  # first Topic channel (already sorted)

    dl_path, actual_duration = _download_with_retry(best["url"], paths.tmp_dir)
    if dl_path:
        from music_manager.services.tagger import strip_youtube_tags  # noqa: PLC0415

        strip_youtube_tags(dl_path)
    if dl_path is None:
        return PendingTrack(
            reason="youtube_failed",
            csv_title=label_title,
            csv_artist=label_artist,
            csv_album=label_album,
            track=track,
            youtube_candidates=candidates[1:],
        )

    # ── Duration check ───────────────────────────────────
    if actual_duration and track.duration:
        ratio = actual_duration / track.duration
        if ratio < _DURATION_RATIO_MIN or ratio > _DURATION_RATIO_MAX:
            return PendingTrack(
                reason="duration_suspect",
                csv_title=label_title,
                csv_artist=label_artist,
                csv_album=label_album,
                track=track,
                dl_path=dl_path,
                actual_duration=actual_duration,
                youtube_candidates=candidates[1:],
            )

    # ── Tag ───────────────────────────────────────────────
    if not tag_audio_file(dl_path, track, cover_path=cover_path):
        log_event("tag_failed", title=track.title, artist=track.artist, path=dl_path)

    # ── Apple Music import ───────────────────────────────
    try:
        apple_id = import_file(dl_path)
    except RuntimeError:
        _cleanup(dl_path)
        return PendingTrack(
            reason="apple_import_failed",
            csv_title=label_title,
            csv_artist=label_artist,
            csv_album=label_album,
            track=track,
        )

    # ── Update store ─────────────────────────────────────
    track.apple_id = apple_id
    track.status = "done"
    track.origin = "imported"
    track.imported_at = datetime.now().isoformat(timespec="seconds")
    track.csv_title = label_title
    track.csv_artist = label_artist
    track.csv_album = label_album

    tracks_store.add(apple_id, track.to_dict())

    log_event(
        "import_done",
        isrc=track.isrc,
        title=track.title,
        artist=track.artist,
        apple_id=apple_id,
    )

    # ── Cleanup (audio file only — cover reused across album tracks) ──
    _cleanup(dl_path)

    return None


# ── Public helpers ───────────────────────────────────────────────────────────


def download_cover(track: Track, paths: Paths, albums_store: Albums) -> str:
    """Download album cover to .tmp/. Returns file path or empty string.

    Reuses existing cover file if already downloaded for this album
    (one download per album, shared across all tracks).
    """
    album_data = albums_store.get(track.album_id)
    cover_url = album_data.get("cover_url", "") if album_data else track.cover_url
    if not cover_url:
        return ""

    # Reuse existing cover for same album (skip cache if album_id=0 — unidentified)
    if track.album_id:
        for ext in (".jpg", ".png"):
            existing = os.path.join(paths.tmp_dir, f"cover_{track.album_id}{ext}")
            if os.path.isfile(existing):
                return existing

    from music_manager.services.resolver import download_cover_file  # noqa: PLC0415

    unique_id = track.album_id or track.isrc or track.deezer_id
    cover_name = f"cover_{unique_id}"
    return download_cover_file(cover_url, paths.tmp_dir, cover_name)


def _download_with_retry(url: str, output_dir: str) -> tuple[str | None, int | None]:
    """Download with exponential backoff (3 attempts: 3s, 9s delays)."""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            return download_track(url, output_dir)
        except RuntimeError:
            if attempt < max_attempts - 1:
                time.sleep(3 ** (attempt + 1))  # 3s, 9s
                continue
            return None, None
    return None, None  # pragma: no cover


def _cleanup(*paths: str) -> None:
    """Remove temporary files."""
    for path in paths:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


def cleanup_covers(tmp_dir: str) -> None:
    """Remove all cached cover files from .tmp/. Call after batch import."""
    if not os.path.isdir(tmp_dir):
        return
    for name in os.listdir(tmp_dir):
        if name.startswith("cover_"):
            try:
                os.remove(os.path.join(tmp_dir, name))
            except OSError:
                pass
