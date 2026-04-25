"""Parallel import pipeline — three-stage concurrent track import.

Stage 1 (Searcher):   YouTube search + cover download — 1 thread, throttled
Stage 2 (Downloader): YouTube download + strip tags — N threads, parallel
Stage 3 (Importer):   Mutagen tag + Apple Music import — 1 thread, sequential

Bounded queues between stages allow search/download/import to overlap.
Crash safety: tracks_store saved every _SAVE_INTERVAL successful imports.
"""

import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from music_manager.core.config import Paths
from music_manager.core.logger import log_event, log_worker_error
from music_manager.core.models import PendingTrack, Track
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

# ── Constants ────────────────────────────────────────────────────────────────

_DOWNLOAD_WORKERS = 2
_QUEUE_SIZE = 5
_SAVE_INTERVAL = 10
_DOWNLOAD_RETRIES = 3
_RETRY_DELAYS = (3, 9)  # seconds between retries
_DURATION_RATIO_MIN = 0.93
_DURATION_RATIO_MAX = 1.07
_POLL_TIMEOUT = 1.0  # seconds — queue poll interval for cancel checks

_STOP = object()  # sentinel signaling end of queue


# ── Result type ──────────────────────────────────────────────────────────────


@dataclass
class BatchResult:
    """Summary of a pipelined batch import."""

    imported: int = 0
    pending: list[PendingTrack] = field(default_factory=list)


# ── Internal data flowing between stages ─────────────────────────────────────


@dataclass
class _SearchResult:
    track: Track
    cover_path: str
    yt_url: str
    yt_candidates: list[dict]
    csv_title: str
    csv_artist: str
    csv_album: str


@dataclass
class _DownloadResult:
    track: Track
    cover_path: str
    audio_path: str
    actual_duration: int | None
    csv_title: str
    csv_artist: str
    csv_album: str


# ── Entry point ──────────────────────────────────────────────────────────────


def run_import_pipeline(
    tracks: list[tuple[Track, str, str, str]],
    paths: Paths,
    tracks_store: Tracks,
    albums_store: Albums,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> BatchResult:
    """Import tracks using a parallel three-stage pipeline.

    Args:
        tracks: list of (Track, csv_title, csv_artist, csv_album) tuples.
        paths: application paths.
        tracks_store: tracks store (thread-safe via RLock).
        albums_store: albums store (thread-safe via RLock).
        on_progress: callback(done, total) called after each track completes.
        should_cancel: returns True to request graceful cancellation.

    Returns:
        BatchResult with imported count and pending tracks.
    """
    if not tracks:
        return BatchResult()

    total = len(tracks)
    cancel = threading.Event()
    result = BatchResult()
    result_lock = threading.Lock()
    done_count = [0]  # mutable counter shared across threads

    # Bounded queues between stages
    search_q: queue.Queue[tuple[Track, str, str, str] | object] = queue.Queue(
        maxsize=_QUEUE_SIZE,
    )
    download_q: queue.Queue[_SearchResult | object] = queue.Queue(
        maxsize=_QUEUE_SIZE,
    )
    import_q: queue.Queue[_DownloadResult | object] = queue.Queue(
        maxsize=_QUEUE_SIZE,
    )

    # Track active downloaders for clean shutdown
    active_downloaders = [_DOWNLOAD_WORKERS]
    active_lock = threading.Lock()

    def is_cancelled() -> bool:
        if cancel.is_set():
            return True
        if should_cancel and should_cancel():
            cancel.set()
            return True
        return False

    def safe_put(target_q: queue.Queue, item: object) -> bool:
        """Put item in queue, respecting cancellation. Returns False if cancelled."""
        while not is_cancelled():
            try:
                target_q.put(item, timeout=_POLL_TIMEOUT)
                return True
            except queue.Full:
                continue
        return False

    def record_pending(pending: PendingTrack) -> None:
        with result_lock:
            result.pending.append(pending)
            done_count[0] += 1
            if on_progress:
                on_progress(done_count[0], total)

    def record_success() -> None:
        with result_lock:
            result.imported += 1
            done_count[0] += 1
            if on_progress:
                on_progress(done_count[0], total)

    def make_pending(
        track: Track, reason: str, csv_title: str, csv_artist: str, csv_album: str,
        dl_path: str = "", actual_duration: int | None = None,
        yt_candidates: list[dict] | None = None,
    ) -> PendingTrack:
        return PendingTrack(
            reason=reason,
            csv_title=csv_title or track.title,
            csv_artist=csv_artist or track.artist,
            csv_album=csv_album or track.album,
            track=track,
            dl_path=dl_path,
            actual_duration=actual_duration or 0,
            youtube_candidates=yt_candidates or [],
        )

    # ── Stage 1: Searcher ────────────────────────────────────────────────

    def searcher() -> None:
        from music_manager.pipeline.importer import download_cover  # noqa: PLC0415
        from music_manager.services.youtube import search_by_isrc  # noqa: PLC0415

        try:
            while True:
                try:
                    item = search_q.get(timeout=_POLL_TIMEOUT)
                except queue.Empty:
                    if is_cancelled():
                        break
                    continue

                if item is _STOP:
                    break
                if is_cancelled():
                    break

                track, csv_title, csv_artist, csv_album = item  # type: ignore[misc]

                # Cover download (cached per album_id)
                cover_path = download_cover(track, paths, albums_store)

                # YouTube search (throttled globally)
                candidates = search_by_isrc(track.isrc)

                if not candidates:
                    record_pending(make_pending(
                        track, "youtube_failed", csv_title, csv_artist, csv_album,
                    ))
                    continue

                sr = _SearchResult(
                    track=track,
                    cover_path=cover_path,
                    yt_url=candidates[0]["url"],
                    yt_candidates=candidates[1:],
                    csv_title=csv_title,
                    csv_artist=csv_artist,
                    csv_album=csv_album,
                )
                if not safe_put(download_q, sr):
                    break
        except Exception as exc:  # noqa: BLE001
            log_worker_error(exc)
        finally:
            # Signal all downloaders to stop
            for _ in range(_DOWNLOAD_WORKERS):
                try:
                    download_q.put(_STOP, timeout=5)
                except queue.Full:
                    pass

    # ── Stage 2: Downloader ──────────────────────────────────────────────

    def downloader() -> None:
        from music_manager.services.tagger import strip_youtube_tags  # noqa: PLC0415
        from music_manager.services.youtube import download_track  # noqa: PLC0415

        try:
            while True:
                try:
                    item = download_q.get(timeout=_POLL_TIMEOUT)
                except queue.Empty:
                    if is_cancelled():
                        break
                    continue

                if item is _STOP:
                    break
                if is_cancelled():
                    break

                sr: _SearchResult = item  # type: ignore[assignment]

                # Download with retry
                dl_path, actual_duration = _download_with_retry(
                    sr.yt_url, paths.tmp_dir, download_track,
                )

                if dl_path:
                    strip_youtube_tags(dl_path)

                if dl_path is None:
                    record_pending(make_pending(
                        sr.track, "youtube_failed",
                        sr.csv_title, sr.csv_artist, sr.csv_album,
                        yt_candidates=sr.yt_candidates,
                    ))
                    continue

                # Duration check
                if actual_duration and sr.track.duration:
                    ratio = actual_duration / sr.track.duration
                    if ratio < _DURATION_RATIO_MIN or ratio > _DURATION_RATIO_MAX:
                        record_pending(make_pending(
                            sr.track, "duration_suspect",
                            sr.csv_title, sr.csv_artist, sr.csv_album,
                            dl_path=dl_path, actual_duration=actual_duration,
                            yt_candidates=sr.yt_candidates,
                        ))
                        continue

                dr = _DownloadResult(
                    track=sr.track,
                    cover_path=sr.cover_path,
                    audio_path=dl_path,
                    actual_duration=actual_duration,
                    csv_title=sr.csv_title,
                    csv_artist=sr.csv_artist,
                    csv_album=sr.csv_album,
                )
                if not safe_put(import_q, dr):
                    break

        except Exception as exc:  # noqa: BLE001
            log_worker_error(exc)
        finally:
            # Last downloader signals the importer
            with active_lock:
                active_downloaders[0] -= 1
                if active_downloaders[0] == 0:
                    try:
                        import_q.put(_STOP, timeout=5)
                    except queue.Full:
                        pass

    # ── Stage 3: Importer ────────────────────────────────────────────────

    def importer() -> None:
        from music_manager.services.apple import import_file  # noqa: PLC0415
        from music_manager.services.tagger import tag_audio_file  # noqa: PLC0415

        imports_since_save = 0

        try:
            while True:
                try:
                    item = import_q.get(timeout=_POLL_TIMEOUT)
                except queue.Empty:
                    if is_cancelled():
                        break
                    continue

                if item is _STOP:
                    break
                if is_cancelled():
                    break

                dr: _DownloadResult = item  # type: ignore[assignment]

                # Tag audio file
                if not tag_audio_file(dr.audio_path, dr.track, cover_path=dr.cover_path):
                    log_event(
                        "tag_failed",
                        title=dr.track.title,
                        artist=dr.track.artist,
                        path=dr.audio_path,
                    )

                # Apple Music import (sequential — OS limitation)
                try:
                    apple_id = import_file(dr.audio_path)
                except RuntimeError:
                    _cleanup_file(dr.audio_path)
                    record_pending(make_pending(
                        dr.track, "apple_import_failed",
                        dr.csv_title, dr.csv_artist, dr.csv_album,
                    ))
                    continue

                # Update store
                dr.track.apple_id = apple_id
                dr.track.status = "done"
                dr.track.origin = "imported"
                dr.track.imported_at = datetime.now().isoformat(timespec="seconds")
                dr.track.csv_title = dr.csv_title or dr.track.title
                dr.track.csv_artist = dr.csv_artist or dr.track.artist
                dr.track.csv_album = dr.csv_album or dr.track.album

                tracks_store.add(apple_id, dr.track.to_dict())

                log_event(
                    "import_done",
                    isrc=dr.track.isrc,
                    title=dr.track.title,
                    artist=dr.track.artist,
                    apple_id=apple_id,
                )

                _cleanup_file(dr.audio_path)
                record_success()

                # Periodic crash-safe save
                imports_since_save += 1
                if imports_since_save >= _SAVE_INTERVAL:
                    tracks_store.save()
                    imports_since_save = 0

        except Exception as exc:  # noqa: BLE001
            log_worker_error(exc)

    # ── Launch pipeline ──────────────────────────────────────────────────

    threads: list[threading.Thread] = []

    t_search = threading.Thread(target=searcher, name="pipeline-search", daemon=True)
    threads.append(t_search)

    for i in range(_DOWNLOAD_WORKERS):
        t_dl = threading.Thread(
            target=downloader, name=f"pipeline-download-{i}", daemon=True,
        )
        threads.append(t_dl)

    t_import = threading.Thread(target=importer, name="pipeline-import", daemon=True)
    threads.append(t_import)

    for t in threads:
        t.start()

    # Feed tracks into search queue
    for item in tracks:
        if is_cancelled():
            break
        safe_put(search_q, item)

    # Signal end of input
    safe_put(search_q, _STOP)

    # Wait for all threads to finish
    for t in threads:
        t.join()

    # Final save
    tracks_store.save()

    return result


# ── Private Functions ────────────────────────────────────────────────────────


def _download_with_retry(
    url: str,
    output_dir: str,
    download_fn: Callable[[str, str], tuple[str, int | None]],
) -> tuple[str | None, int | None]:
    """Download with exponential backoff (3 attempts: 3s, 9s delays)."""
    for attempt in range(_DOWNLOAD_RETRIES):
        try:
            return download_fn(url, output_dir)
        except RuntimeError:
            if attempt < _DOWNLOAD_RETRIES - 1:
                time.sleep(_RETRY_DELAYS[attempt])
                continue
            return None, None
    return None, None  # pragma: no cover


def _cleanup_file(filepath: str) -> None:
    """Remove a single temporary file."""
    if filepath:
        try:
            os.remove(filepath)
        except OSError:
            pass
