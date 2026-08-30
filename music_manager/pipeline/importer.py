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
from music_manager.services.youtube import (
    ERROR_NOT_FOUND,
    ERROR_OTHER,
    ERROR_RATE_LIMITED,
    ERROR_TIMEOUT,
    DownloadError,
    download_track,
    search_by_isrc_detailed,
)

# ── Constants ────────────────────────────────────────────────────────────────

_DURATION_RATIO_MIN = 0.93
_DURATION_RATIO_MAX = 1.07

_DOWNLOAD_MAX_ATTEMPTS = 3

# Retrying only helps when the failure is transient. A 403 or a deleted video
# answers identically every time.
_RETRYABLE_DOWNLOAD_ERRORS = frozenset({ERROR_TIMEOUT, ERROR_RATE_LIMITED, ERROR_OTHER})


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
    candidates, search_error = search_by_isrc_detailed(track.isrc)
    if not candidates:
        return PendingTrack(
            reason="youtube_failed",
            # "" means the search was clean and the ISRC simply isn't on
            # YouTube — a very different problem from being blocked.
            detail=search_error or ERROR_NOT_FOUND,
            csv_title=label_title,
            csv_artist=label_artist,
            csv_album=label_album,
            track=track,
        )

    dl_path, actual_duration, used_index, download_error = _download_first_usable(
        candidates, paths.tmp_dir
    )
    if dl_path is None:
        return PendingTrack(
            reason="youtube_failed",
            detail=download_error or ERROR_OTHER,
            csv_title=label_title,
            csv_artist=label_artist,
            csv_album=label_album,
            track=track,
            youtube_candidates=candidates,
        )

    from music_manager.services.tagger import strip_youtube_tags  # noqa: PLC0415

    strip_youtube_tags(dl_path)

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
                youtube_candidates=[
                    candidate for index, candidate in enumerate(candidates) if index != used_index
                ],
            )

    # ── Tag ───────────────────────────────────────────────
    if not tag_audio_file(dl_path, track, cover_path=cover_path):
        log_event("tag_failed", title=track.title, artist=track.artist, path=dl_path)

    # ── Apple Music import ───────────────────────────────
    try:
        apple_id = import_file(dl_path)
    except RuntimeError as exc:
        _cleanup(dl_path)
        log_event(
            "apple_import_failed",
            title=track.title,
            artist=track.artist,
            error=str(exc)[:200],
        )
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

    from music_manager.services.resolver import (  # noqa: PLC0415
        download_cover_file,
        search_itunes_covers,
    )

    unique_id = track.album_id or track.isrc or track.deezer_id
    cover_name = f"cover_{unique_id}"
    cover_path = download_cover_file(cover_url, paths.tmp_dir, cover_name)
    if cover_path:
        return cover_path

    # Best-effort fallback: the cached Deezer/iTunes URL can be stale or
    # blocked. Re-query iTunes once and try the best matching artwork URL.
    for item in search_itunes_covers(track.album, track.artist):
        fallback_url = str(item.get("url") or "")
        if not fallback_url or fallback_url == cover_url:
            continue
        cover_path = download_cover_file(fallback_url, paths.tmp_dir, cover_name)
        if cover_path:
            log_event(
                "cover_fallback_used",
                isrc=track.isrc,
                album=track.album,
                artist=track.artist,
            )
            return cover_path

    log_event(
        "cover_download_failed",
        isrc=track.isrc,
        album=track.album,
        artist=track.artist,
    )
    return ""


def _download_first_usable(
    candidates: list[dict], output_dir: str
) -> tuple[str | None, int | None, int, str]:
    """Try each candidate in order. Returns ``(path, duration, index, error)``.

    ``index`` is the position of the candidate that worked (``-1`` on total
    failure) and ``error`` the code of the last failure.
    """
    last_error = ""
    for index, candidate in enumerate(candidates):
        url = str(candidate.get("url") or "")
        if not url:
            continue
        dl_path, duration, error = _download_with_retry(url, output_dir)
        if dl_path is not None:
            return dl_path, duration, index, ""
        last_error = error
        log_event("youtube_candidate_rejected", url=url, code=error, rank=index)
    return None, None, -1, last_error


def _download_with_retry(url: str, output_dir: str) -> tuple[str | None, int | None, str]:
    """Download with exponential backoff. Returns ``(path, duration, error_code)``.

    Only transient failures are retried. Re-requesting a URL that YouTube
    refuses to serve (403) or that no longer exists just burns ~15s and three
    more requests against the rate limiter, so those fail fast and let the
    caller move to the next candidate.
    """
    for attempt in range(_DOWNLOAD_MAX_ATTEMPTS):
        try:
            dl_path, duration = download_track(url, output_dir)
        except DownloadError as exc:
            if exc.code not in _RETRYABLE_DOWNLOAD_ERRORS:
                return None, None, exc.code
            if attempt < _DOWNLOAD_MAX_ATTEMPTS - 1:
                time.sleep(3 ** (attempt + 1))  # 3s, 9s
                continue
            return None, None, exc.code
        except RuntimeError:
            if attempt < _DOWNLOAD_MAX_ATTEMPTS - 1:
                time.sleep(3 ** (attempt + 1))
                continue
            return None, None, ERROR_OTHER
        else:
            return dl_path, duration, ""
    return None, None, ERROR_OTHER  # pragma: no cover


def discard_pending(pending: PendingTrack | None) -> None:
    """Delete the audio file a PendingTrack was holding on to.

    ``PendingTrack`` keeps ``dl_path`` alive so the Textual review screen can
    let the user listen to a duration-suspect download. Callers without a
    review step (the widget worker) must call this or the .m4a leaks into
    ``.tmp/`` forever.
    """
    if pending and pending.dl_path:
        _cleanup(pending.dl_path)
        pending.dl_path = ""


def _cleanup(*paths: str) -> None:
    """Remove temporary files."""
    for path in paths:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


def cleanup_covers(tmp_dir: str) -> None:
    """Remove all cached cover files from .tmp/. Call after batch import.

    Covers the playlist artwork too: ``_try_set_playlist_cover`` downloads it
    as ``playlist_cover.jpg`` and nothing else ever deletes it.
    """
    if not os.path.isdir(tmp_dir):
        return
    for name in os.listdir(tmp_dir):
        if name.startswith(("cover_", "playlist_cover")):
            try:
                os.remove(os.path.join(tmp_dir, name))
            except OSError:
                pass
