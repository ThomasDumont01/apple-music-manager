"""Complete albums — import missing tracks from identified albums (§8)."""

import functools
from collections.abc import Callable
from dataclasses import dataclass, field

from music_manager.core.config import Paths
from music_manager.core.models import PendingTrack
from music_manager.options.import_tracks import remove_failed
from music_manager.pipeline.dedup import is_duplicate
from music_manager.pipeline.executor import run_import_pipeline
from music_manager.services.albums import Albums
from music_manager.services.resolver import (
    build_track,
    fetch_album_with_cover,
    get_album_tracklist,
)
from music_manager.services.tracks import Tracks

# ── Result type ──────────────────────────────────────────────────────────────


@dataclass
class CompleteResult:
    """Summary of album completion."""

    albums_complete: int = 0
    tracks_imported: int = 0
    pending: list[PendingTrack] = field(default_factory=list)


# ── Entry point ──────────────────────────────────────────────────────────────


def find_incomplete_albums(
    tracks_store: Tracks,
    albums_store: Albums,
) -> list[dict]:
    """Find albums with missing tracks. Returns list of {album_id, title,
    artist, local, total, missing}."""
    tracks = tracks_store.all()

    # Group identified tracks by album_id (exclude failed)
    albums: dict[int, list[dict]] = {}
    for entry in tracks.values():
        album_id = entry.get("album_id")
        if not album_id or not entry.get("deezer_id"):
            continue
        if entry.get("status") == "failed":
            continue
        albums.setdefault(album_id, []).append(entry)

    incomplete = []
    for album_id, entries in albums.items():
        album_data = fetch_album_with_cover(album_id, albums_store)
        if not album_data:
            continue

        total = album_data.get("total_tracks", 0)
        if not total:
            continue

        # Count truly missing tracks using dedup (accounts for cross-album ISRCs)
        tracklist = get_album_tracklist(album_id, albums_store)
        if not tracklist:
            continue

        missing_count = 0
        for dz_track in tracklist:
            isrc = dz_track.get("isrc", "")
            title = dz_track.get("title", "")
            artist = dz_track.get("artist", {}).get("name", "")
            if not is_duplicate(isrc, title, artist, tracks_store):
                missing_count += 1

        if missing_count == 0:
            continue

        incomplete.append(
            {
                "album_id": album_id,
                "title": album_data.get("title", ""),
                "artist": album_data.get("artist", ""),
                "local": total - missing_count,
                "total": total,
            }
        )

    def _sort_cmp(a: dict, b: dict) -> int:
        ta, tb = a.get("title", ""), b.get("title", "")
        da, db = ta[:1].isdigit(), tb[:1].isdigit()
        if da != db:
            return 1 if da else -1
        return _apple_cmp(ta, tb)

    incomplete.sort(key=functools.cmp_to_key(_sort_cmp))
    return incomplete


def complete_album(
    album_id: int,
    paths: Paths,
    tracks_store: Tracks,
    albums_store: Albums,
    on_progress: Callable[[int, int], None] | None = None,
    preferences_path: str = "",
    should_cancel: Callable[[], bool] | None = None,
) -> CompleteResult:
    """Import missing tracks for one album."""
    result = CompleteResult()

    # Get album data + tracklist (cached in albums_store)
    album_data = fetch_album_with_cover(album_id, albums_store)
    deezer_tracks = get_album_tracklist(album_id, albums_store)
    if not deezer_tracks:
        return result

    # Check if user refused cover for this album → use existing cover instead
    _override_cover_from_refusals(
        album_id,
        album_data or {},
        tracks_store,
        paths,
        preferences_path,
    )

    # Filter to only missing tracks
    missing = []
    for dz_track in deezer_tracks:
        isrc = dz_track.get("isrc", "")
        title = dz_track.get("title", "")
        artist = dz_track.get("artist", {}).get("name", "")

        if is_duplicate(isrc, title, artist, tracks_store):
            continue
        missing.append(dz_track)

    if not missing:
        return result

    # Log each missing track before pipeline starts
    from music_manager.core.logger import log_event  # noqa: PLC0415

    album_title = (album_data or {}).get("title", "")
    missing_titles = [m.get("title", "") for m in missing]
    log_event(
        "complete_missing_tracks",
        album_id=album_id,
        album=album_title,
        count=len(missing),
        tracks=missing_titles,
    )

    # Remove previously failed imports before pipeline
    for dz_track in missing:
        isrc = dz_track.get("isrc", "")
        title = dz_track.get("title", "")
        artist = dz_track.get("artist", {}).get("name", "")
        remove_failed(isrc, title, artist, tracks_store)

    # Build Track objects for the pipeline
    tracks_for_pipeline = [
        (build_track(dz, album_data or {}), "", "", "")
        for dz in missing
    ]

    # Run parallel import pipeline
    batch = run_import_pipeline(
        tracks_for_pipeline,
        paths,
        tracks_store,
        albums_store,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )

    result.tracks_imported = batch.imported
    result.pending = batch.pending

    if result.tracks_imported > 0:
        result.albums_complete += 1

    return result


# ── Private Functions ────────────────────────────────────────────────────────

_ARTICLE_PREFIXES = ("a ", "an ", "the ", "le ", "la ", "les ", "un ", "une ", "de ")


def _strip_article(title: str) -> str:
    """Strip leading article for sort (same behavior as Apple Music)."""
    lower = title.lower()
    for art in _ARTICLE_PREFIXES:
        if lower.startswith(art):
            return title[len(art):]
    if lower.startswith("l\u2019") or lower.startswith("l'"):
        return title[2:]
    return title


def _apple_cmp(a: str, b: str) -> int:
    """Compare two strings using macOS localized sort + article stripping (Apple Music order)."""
    from Foundation import NSString  # type: ignore[import-untyped]  # noqa: PLC0415

    ns_a = NSString.alloc().initWithString_(_strip_article(a))
    ns_b = NSString.alloc().initWithString_(_strip_article(b))
    return ns_a.localizedStandardCompare_(ns_b)


def _override_cover_from_refusals(
    album_id: int,
    album_data: dict,
    tracks_store: Tracks,
    paths: Paths,
    preferences_path: str,
) -> None:
    """If user refused cover fix for this album, skip cover download.

    New tracks will inherit the album cover from Apple Music automatically
    (Apple Music assigns the album's existing cover to new tracks in the same album).
    """
    if not preferences_path:
        return

    from music_manager.core.io import load_json  # noqa: PLC0415

    prefs = load_json(preferences_path)
    raw = prefs.get("refusals")
    refusals: dict = raw if isinstance(raw, dict) else {}
    if not refusals:
        return

    # Find if any track of this album has a cover refusal
    for apple_id, entry in tracks_store.all().items():
        if entry.get("album_id") != album_id:
            continue
        if f"{apple_id}:cover" in refusals:
            # User prefers existing cover → don't embed Deezer/iTunes cover
            album_data["cover_url"] = ""
            return


def _find_mp3_in_album(album_id: int, tracks_store: Tracks) -> list[tuple[str, str]]:
    """Find MP3 tracks in an album. Returns [(apple_id, file_path), ...]."""
    result = []
    for apple_id, entry in tracks_store.all().items():
        if entry.get("album_id") != album_id:
            continue
        fp = entry.get("file_path", "")
        if fp and fp.lower().endswith(".mp3"):
            result.append((apple_id, fp))
    return result


def _convert_mp3_to_m4a(
    mp3_tracks: list[tuple[str, str]],
    tracks_store: Tracks,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Convert MP3 tracks to M4A. Returns count of successful conversions.

    For each MP3: ffmpeg convert → import new M4A → delete old MP3.
    Apple Music then sees all tracks as AAC → same album grouping.
    """
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from music_manager.services.apple import (  # noqa: PLC0415
        delete_tracks,
        get_playlist_membership,
        import_file,
        rebuild_playlist,
        run_applescript,
    )

    converted = 0
    for idx, (apple_id, mp3_path) in enumerate(mp3_tracks):
        if on_progress:
            on_progress(idx + 1, len(mp3_tracks))

        if not os.path.isfile(mp3_path):
            continue

        tmp_m4a = ""
        try:
            # Convert MP3 → M4A
            tmp_m4a = tempfile.NamedTemporaryFile(suffix=".m4a", delete=False).name
            subprocess.run(
                ["ffmpeg", "-i", mp3_path, "-c:a", "aac", "-b:a", "256k", "-vn", tmp_m4a, "-y"],
                capture_output=True,
                timeout=120,
                check=True,
            )

            # Copy metadata from store to new M4A
            from mutagen.mp3 import MP3  # noqa: PLC0415
            from mutagen.mp4 import MP4, MP4Cover  # noqa: PLC0415

            entry = tracks_store.get_by_apple_id(apple_id) or {}
            m4a = MP4(tmp_m4a)
            m4a["\xa9nam"] = [entry.get("title", "")]
            m4a["\xa9ART"] = [entry.get("artist", "")]
            m4a["\xa9alb"] = [entry.get("album", "")]
            if entry.get("genre"):
                m4a["\xa9gen"] = [entry["genre"]]
            if entry.get("release_date"):
                m4a["\xa9day"] = [entry["release_date"][:4]]
            if entry.get("album_artist"):
                m4a["aART"] = [entry["album_artist"]]
            trk = entry.get("track_number")
            tot = entry.get("total_tracks")
            if trk:
                m4a["trkn"] = [(int(trk), int(tot or 0))]
            disk = entry.get("disk_number")
            if disk:
                m4a["disk"] = [(int(disk), int(entry.get("total_discs") or 0))]
            isrc = entry.get("isrc", "")
            if isrc:
                m4a["----:com.apple.iTunes:ISRC"] = [isrc.encode("utf-8")]
            if entry.get("explicit"):
                m4a["rtng"] = [1]

            # Copy cover from MP3 if present
            try:
                mp3 = MP3(mp3_path)
                if mp3.tags:
                    for key in mp3.tags:
                        if key.startswith("APIC"):
                            apic = mp3.tags[key]
                            fmt = (
                                MP4Cover.FORMAT_PNG if "png" in apic.mime else MP4Cover.FORMAT_JPEG
                            )
                            m4a["covr"] = [MP4Cover(apic.data, imageformat=fmt)]
                            break
            except Exception:  # noqa: BLE001
                pass

            m4a.save()

            # Import new → delete old (import first for safety)
            playlists = get_playlist_membership(apple_id)
            new_apple_id = import_file(tmp_m4a)

            from music_manager.services.apple import _esc  # noqa: PLC0415

            refresh_script = (
                'tell application "Music"\n'
                "    set t to first track of library playlist 1"
                f' whose persistent ID is "{_esc(new_apple_id)}"\n'
                "    refresh t\n"
                "end tell"
            )
            run_applescript(refresh_script)

            # Update store
            tracks_store.remove(apple_id)
            delete_tracks([apple_id])
            entry["file_path"] = ""
            tracks_store.add(new_apple_id, entry)
            tracks_store.save()

            # Restore playlists
            for playlist_name, ordered_ids in playlists:
                new_ids = [new_apple_id if aid == apple_id else aid for aid in ordered_ids]
                rebuild_playlist(playlist_name, new_ids)

            converted += 1
        except Exception as exc:  # noqa: BLE001
            from music_manager.core.logger import log_event  # noqa: PLC0415

            log_event("mp3_to_m4a_failed", apple_id=apple_id, error=str(exc))
        finally:
            try:
                os.unlink(tmp_m4a)
            except OSError:
                pass

    if converted:
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("mp3_to_m4a_batch", converted=converted, total=len(mp3_tracks))

    return converted
