"""Fix metadata — correct identified tracks against Deezer data (§5).

Only works on tracks with a deezer_id. Detects divergences field by field,
groups by album, and applies user-selected corrections.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from music_manager.core.io import load_json, save_json
from music_manager.core.models import LibraryEntry
from music_manager.services.albums import Albums
from music_manager.services.apple import (
    Apple,
    set_artwork_batch,
    update_tracks_batch,
)
from music_manager.services.resolver import deezer_get, fetch_album_with_cover
from music_manager.services.tagger import get_cover_dimensions, write_cover
from music_manager.services.tracks import Tracks

_EMPTY_ENTRY = LibraryEntry(apple_id="", title="", artist="", album="")

# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class Divergence:
    """A single field divergence on a track."""

    apple_id: str
    field_name: str
    local_value: str
    deezer_value: str


@dataclass
class AlbumDivergences:
    """All divergences for one album."""

    album_title: str
    artist: str
    track_count: int
    divergences: list[Divergence] = field(default_factory=list)
    cover_url: str = ""


@dataclass
class FixResult:
    """Summary of a fix-metadata run."""

    corrected: int = 0
    up_to_date: int = 0
    skipped: int = 0


# ── Entry point ──────────────────────────────────────────────────────────────


def find_all_divergences(
    tracks_store: Tracks,
    albums_store: Albums,
    apple_store: Apple,
    preferences_path: str,
    on_fetch: Callable[[int, int], None] | None = None,
) -> list[AlbumDivergences]:
    """Detect metadata divergences for all identified tracks, grouped by album.

    Filters out already-refused corrections and ignored albums.
    """
    import time as _time  # noqa: PLC0415

    from music_manager.core.logger import log_event  # noqa: PLC0415

    _t_start = _time.perf_counter()
    preferences = load_json(preferences_path)
    raw_ignored = preferences.get("ignored_albums", [])
    ignored_albums: list = raw_ignored if isinstance(raw_ignored, list) else []
    raw_refusals = preferences.get("refusals", {})
    refusals: dict = raw_refusals if isinstance(raw_refusals, dict) else {}

    library = apple_store.get_all()
    tracks = tracks_store.all()

    # Silent fix: normalize string types + write missing ISRCs to MP3 files
    _auto_fix_store(tracks, tracks_store, library)

    # Group identified tracks by album_id (only tracks with deezer_id + album_id)
    album_tracks: dict[int, list[tuple[str, dict]]] = {}
    for apple_id, entry in tracks.items():
        deezer_id = entry.get("deezer_id")
        album_id = entry.get("album_id")
        if not deezer_id or not album_id:
            continue
        album_tracks.setdefault(album_id, []).append((apple_id, entry))

    # Process album by album
    albums_divs: dict[int, AlbumDivergences] = {}

    # Count albums needing API fetch (not in cache)
    uncached = [
        aid
        for aid in album_tracks
        if not albums_store.get(aid) or "_tracklist" not in (albums_store.get(aid) or {})
    ]
    fetch_idx = 0

    for album_id, entries_list in album_tracks.items():
        needs_fetch = album_id in uncached
        if needs_fetch and on_fetch:
            fetch_idx += 1
            on_fetch(fetch_idx, len(uncached))

        album_data = fetch_album_with_cover(album_id, albums_store)
        if not album_data:
            continue

        album_title = album_data.get("title", "")
        if album_title in ignored_albums:
            continue

        # Fetch Deezer tracks for this album (cached in album_data)
        cached_tracks = album_data.get("_tracklist")
        if cached_tracks is None:
            tracklist_data = deezer_get(f"/album/{album_id}/tracks?limit=100")
            if not tracklist_data:
                continue
            cached_tracks = tracklist_data.get("data", [])
            album_data["_tracklist"] = cached_tracks
            albums_store.put(album_id, album_data)
        deezer_tracks = {item.get("id"): item for item in cached_tracks}

        album_group = AlbumDivergences(
            album_title=album_title,
            artist=album_data.get("artist", ""),
            track_count=len(entries_list),
            cover_url=album_data.get("cover_url", ""),
        )

        for apple_id, entry in entries_list:
            lib_entry = library.get(apple_id)
            if not lib_entry:
                continue

            deezer_id = entry.get("deezer_id")
            track_data = deezer_tracks.get(deezer_id, {})
            if not track_data:
                continue

            comparisons = [
                ("title", lib_entry.title, track_data.get("title", "")),
                ("artist", lib_entry.artist, track_data.get("artist", {}).get("name", "")),
                ("album", lib_entry.album, album_title),
                ("genre", lib_entry.genre, album_data.get("genre", "")),
                ("year", lib_entry.year, album_data.get("year", "")),
                (
                    "track_number",
                    str(lib_entry.track_number or ""),
                    str(track_data.get("track_position") or ""),
                ),
                (
                    "disk_number",
                    str(lib_entry.disk_number or ""),
                    str(track_data.get("disk_number") or ""),
                ),
                (
                    "total_tracks",
                    str(lib_entry.total_tracks or ""),
                    str(album_data.get("total_tracks") or ""),
                ),
                ("album_artist", lib_entry.album_artist, album_data.get("album_artist", "")),
                (
                    "explicit",
                    str(lib_entry.explicit),
                    str(track_data.get("explicit_lyrics", False)),
                ),
            ]

            for field_name, local_val, deezer_val in comparisons:
                if not deezer_val or local_val == deezer_val:
                    continue
                refusal_key = f"{apple_id}:{field_name}"
                if refusals.get(refusal_key) == deezer_val:
                    continue
                album_group.divergences.append(
                    Divergence(apple_id, field_name, str(local_val), str(deezer_val))
                )

        # Add cover divergence if artwork missing or low quality
        if album_group.cover_url:
            first_apple_id = entries_list[0][0]
            first_lib = library.get(first_apple_id, _EMPTY_ENTRY)
            cover_local = ""
            needs_cover = False

            if any(not library.get(aid, _EMPTY_ENTRY).has_artwork for aid, _ in entries_list):
                needs_cover = True  # Missing artwork
            else:
                # Use tracks_store file_path (synced at launch) over Apple cache
                first_entry = tracks.get(first_apple_id, {})
                cover_fp = first_entry.get("file_path") or first_lib.file_path
                cw, ch = get_cover_dimensions(cover_fp) if cover_fp else (0, 0)
                if cw == 0:
                    # Cover unreadable by mutagen — propose fix
                    needs_cover = True
                elif cw < 1000 or ch < 1000 or cw != ch:
                    needs_cover = True
                    cover_local = f"{cw}x{ch}"

            if needs_cover:
                refusal_key = f"{first_apple_id}:cover"
                if refusals.get(refusal_key) != album_group.cover_url:
                    album_group.divergences.append(
                        Divergence(first_apple_id, "cover", cover_local, album_group.cover_url)
                    )

        if album_group.divergences:
            albums_divs[album_id] = album_group

    # Persist album cache to disk (fetched albums stay cached for next run)
    albums_store.save()

    result = list(albums_divs.values())
    total_divs = sum(len(a.divergences) for a in result)
    log_event(
        "fix_scan",
        duration_ms=int((_time.perf_counter() - _t_start) * 1000),
        albums_scanned=len(album_tracks),
        divergences=total_divs,
        albums_with_issues=len(result),
    )
    return result


def apply_corrections(
    corrections: list[Divergence],
    tracks_store: Tracks,
    apple_store: Apple | None = None,
    cover_url: str = "",
    cover_entries: list[str] | None = None,
) -> tuple[int, list[tuple[str, bool]]]:
    """Apply metadata + cover corrections immediately. Queue explicit for later.

    Returns (count applied, explicit_queue) where explicit_queue is
    [(apple_id, is_explicit), ...] to be passed to apply_explicit_batch().
    """
    count = 0
    has_cover = False

    # Separate corrections by type
    by_track: dict[str, dict[str, str]] = {}
    explicit_queue: list[tuple[str, bool]] = []
    for correction in corrections:
        if correction.field_name == "cover":
            has_cover = True
            continue
        if correction.field_name == "explicit":
            explicit_queue.append((correction.apple_id, correction.deezer_value == "True"))
            continue
        by_track.setdefault(correction.apple_id, {})[correction.field_name] = (
            correction.deezer_value
        )

    # Apply metadata fields — batch AppleScript for all tracks at once
    if by_track:
        apple_batch: dict[str, dict] = {}
        for apple_id, fields in by_track.items():
            apple_fields = {}
            for field_name, value in fields.items():
                apple_value = _to_apple_value(field_name, value)
                if apple_value:
                    apple_fields.update(apple_value)
            if apple_fields:
                apple_batch[apple_id] = apple_fields
                tracks_store.update(apple_id, fields)
                if apple_store:
                    lib = apple_store.get_all()
                    entry = lib.get(apple_id)
                    if entry:
                        for field_name, value in fields.items():
                            if hasattr(entry, field_name):
                                setattr(entry, field_name, value)
                count += len(apple_fields)
        if apple_batch:
            update_tracks_batch(apple_batch)

    # Cover update: only if "cover" was in selected corrections
    if has_cover and cover_url and cover_entries:
        import tempfile  # noqa: PLC0415

        from music_manager.services.resolver import download_cover_file  # noqa: PLC0415

        tmp_dir = tempfile.mkdtemp()
        tmp_path = download_cover_file(cover_url, tmp_dir, "cover_fix")
        if not tmp_path:
            from music_manager.core.logger import log_event  # noqa: PLC0415

            log_event("fix_cover_download_failed", cover_url=cover_url)
        try:
            if not tmp_path:
                raise FileNotFoundError("cover download failed")
            # Write cover to audio files via mutagen (M4A + MP3)
            for apple_id in cover_entries:
                entry = tracks_store.get_by_apple_id(apple_id)
                file_path = entry.get("file_path", "") if entry else ""
                if not file_path and apple_store:
                    lib_e = apple_store.get_all().get(apple_id)
                    file_path = lib_e.file_path if lib_e else ""
                if file_path:
                    write_cover(file_path, tmp_path)
                if apple_store:
                    lib_e = apple_store.get_all().get(apple_id)
                    if lib_e:
                        lib_e.has_artwork = True
            # Set artwork in Apple Music — batch single call
            set_artwork_batch(cover_entries, tmp_path)
            count += 1  # counted only after successful application
        except Exception as exc:  # noqa: BLE001
            from music_manager.core.logger import log_event  # noqa: PLC0415

            log_event("fix_cover_failed", cover_url=cover_url, error=str(exc))
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    tracks_store.save()
    return count, explicit_queue


def apply_explicit_batch(
    explicit_queue: list[tuple[str, bool]],
    tracks_store: Tracks,
    apple_store: Apple | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Divergence]:
    """Apply all queued explicit corrections. Returns list of applied Divergences.

    M4A: rtng + refresh (fast, sequential).
    MP3: ffmpeg conversion in parallel, then Apple Music ops sequential.
    """
    applied: list[Divergence] = []
    done = 0
    total = len(explicit_queue)

    # Resolve file paths and split by format
    m4a_items: list[tuple[str, str, bool]] = []  # (apple_id, file_path, is_explicit)
    mp3_items: list[tuple[str, str, bool]] = []

    for apple_id, is_explicit in explicit_queue:
        entry = tracks_store.get_by_apple_id(apple_id)
        file_path = entry.get("file_path", "") if entry else ""
        if not file_path or not os.path.isfile(file_path):
            if apple_store:
                lib_entry = apple_store.get_all().get(apple_id)
                file_path = lib_entry.file_path if lib_entry else ""
        if not file_path or not os.path.isfile(file_path):
            continue
        if file_path.endswith(".m4a"):
            m4a_items.append((apple_id, file_path, is_explicit))
        else:
            mp3_items.append((apple_id, file_path, is_explicit))

    # Phase 1: M4A fixes (fast — rtng + refresh)
    for apple_id, file_path, is_explicit in m4a_items:
        done += 1
        if on_progress:
            on_progress(done, total)
        try:
            _apply_explicit_m4a(apple_id, file_path, is_explicit)
            tracks_store.update(apple_id, {"explicit": is_explicit})
            applied.append(Divergence(apple_id, "explicit", "", str(is_explicit)))
            if apple_store:
                lib_e = apple_store.get_all().get(apple_id)
                if lib_e:
                    lib_e.explicit = is_explicit
        except Exception as exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _log  # noqa: PLC0415

            _log("explicit_m4a_failed", apple_id=apple_id, error=str(exc))

    # Phase 2: MP3 conversions (sequential — avoids ffmpeg CPU contention + Apple Music race)
    for apple_id, fp, is_explicit in mp3_items:
        done += 1
        if on_progress:
            on_progress(done, total)
        try:
            tmp_m4a = _ffmpeg_convert(fp, apple_id, is_explicit, tracks_store)
            new_id = _import_converted(apple_id, tmp_m4a, tracks_store)
            tracks_store.update(new_id, {"explicit": is_explicit})
            applied.append(Divergence(new_id, "explicit", "", str(is_explicit)))
            if apple_store:
                lib_e = apple_store.get_all().get(new_id)
                if lib_e:
                    lib_e.explicit = is_explicit
        except Exception as exc:  # noqa: BLE001
            from music_manager.core.logger import log_event as _log  # noqa: PLC0415

            _log("explicit_convert_failed", apple_id=apple_id, error=str(exc))

    tracks_store.save()
    return applied


def save_refusals(
    refused: list[Divergence],
    preferences_path: str,
) -> None:
    """Save refused corrections to preferences.json."""
    preferences = load_json(preferences_path)
    raw = preferences.get("refusals")
    refusals: dict = raw if isinstance(raw, dict) else {}
    for divergence in refused:
        key = f"{divergence.apple_id}:{divergence.field_name}"
        refusals[key] = divergence.deezer_value
    preferences["refusals"] = refusals
    save_json(preferences_path, preferences)


def ignore_album(album_title: str, preferences_path: str) -> None:
    """Mark an album as permanently ignored."""
    preferences = load_json(preferences_path)
    raw = preferences.get("ignored_albums")
    ignored: list = raw if isinstance(raw, list) else []
    if album_title not in ignored:
        ignored.append(album_title)
    preferences["ignored_albums"] = ignored
    save_json(preferences_path, preferences)


# ── Private Functions ────────────────────────────────────────────────────────


def _to_apple_value(field_name: str, value: str) -> dict:
    """Convert a field name + value to an Apple Music update dict."""
    int_fields = {"track_number", "total_tracks", "disk_number", "year"}
    if field_name in int_fields:
        try:
            return {field_name: int(value)}
        except ValueError:
            return {}
    return {field_name: value}


def _apply_explicit_m4a(apple_id: str, file_path: str, is_explicit: bool) -> str | None:
    """Set explicit on M4A file + refresh Apple Music. Returns None (apple_id unchanged)."""
    from mutagen.mp4 import MP4  # noqa: PLC0415

    from music_manager.services.apple import run_applescript  # noqa: PLC0415

    audio = MP4(file_path)
    audio["rtng"] = [1 if is_explicit else 0]
    audio.save()
    run_applescript(
        'tell application "Music"\n'
        "    set t to first track of library playlist 1"
        f' whose persistent ID is "{apple_id}"\n'
        "    refresh t\n"
        "end tell"
    )
    return None


def _ffmpeg_convert(
    mp3_path: str,
    apple_id: str,
    is_explicit: bool,
    tracks_store: Tracks,
) -> str:
    """Convert MP3 → M4A, copy metadata, set explicit. Returns tmp M4A path.

    Thread-safe: only reads tracks_store and mp3 file, writes to new temp file.
    """
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from mutagen.mp3 import MP3  # noqa: PLC0415
    from mutagen.mp4 import MP4  # noqa: PLC0415

    tmp_m4a = tempfile.NamedTemporaryFile(suffix=".m4a", delete=False).name
    try:
        subprocess.run(
            ["ffmpeg", "-i", mp3_path, "-c:a", "aac", "-b:a", "256k", "-vn", tmp_m4a, "-y"],
            capture_output=True,
            timeout=120,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        os.unlink(tmp_m4a)
        raise RuntimeError(f"ffmpeg conversion failed: {mp3_path}")

    try:
        mp3 = MP3(mp3_path)
    except Exception:  # noqa: BLE001
        mp3 = None

    m4a = MP4(tmp_m4a)
    entry = tracks_store.get_by_apple_id(apple_id) or {}
    m4a["\xa9nam"] = [entry.get("title", "")]
    m4a["\xa9ART"] = [entry.get("artist", "")]
    m4a["\xa9alb"] = [entry.get("album", "")]
    if entry.get("genre"):
        m4a["\xa9gen"] = [entry["genre"]]
    if entry.get("year"):
        m4a["\xa9day"] = [str(entry["year"])]
    if entry.get("album_artist"):
        m4a["aART"] = [entry["album_artist"]]
    track_num = entry.get("track_number")
    total_tracks = entry.get("total_tracks")
    if track_num:
        m4a["trkn"] = [(int(track_num), int(total_tracks or 0))]
    disk_num = entry.get("disk_number")
    if disk_num:
        m4a["disk"] = [(int(disk_num), 0)]
    isrc = entry.get("isrc", "")
    if isrc:
        m4a["----:com.apple.iTunes:ISRC"] = [isrc.encode("utf-8")]
    m4a["rtng"] = [1 if is_explicit else 0]

    if mp3 and mp3.tags:
        for key in mp3.tags:
            if key.startswith("APIC"):
                apic = mp3.tags[key]
                from mutagen.mp4 import MP4Cover  # noqa: PLC0415

                fmt = MP4Cover.FORMAT_PNG if "png" in apic.mime else MP4Cover.FORMAT_JPEG
                m4a["covr"] = [MP4Cover(apic.data, imageformat=fmt)]
                break

    m4a.save()
    return tmp_m4a


def _import_converted(apple_id: str, tmp_m4a: str, tracks_store: Tracks) -> str:
    """Import converted M4A, delete old, restore playlists. Returns new apple_id."""
    from music_manager.services.apple import (  # noqa: PLC0415
        delete_tracks,
        get_playlist_membership,
        import_file,
        rebuild_playlist,
        run_applescript,
    )

    playlists = get_playlist_membership(apple_id)

    # Import new BEFORE deleting old (safety: if import fails, old track preserved)
    new_apple_id = import_file(tmp_m4a)

    run_applescript(
        'tell application "Music"\n'
        f'    set t to first track of library playlist 1 whose persistent ID is "{new_apple_id}"\n'
        "    refresh t\n"
        "end tell"
    )

    # Now safe to delete old
    entry = tracks_store.get_by_apple_id(apple_id) or {}
    tracks_store.remove(apple_id)
    delete_tracks([apple_id])
    entry["file_path"] = ""
    tracks_store.add(new_apple_id, entry)

    for playlist_name, ordered_ids in playlists:
        new_ids = [new_apple_id if aid == apple_id else aid for aid in ordered_ids]
        rebuild_playlist(playlist_name, new_ids)

    try:
        os.unlink(tmp_m4a)
    except OSError:
        pass

    return new_apple_id


def _auto_fix_store(
    tracks: dict[str, dict],
    tracks_store: Tracks,
    library: dict,
) -> None:
    """Silent one-time fix: normalize types + write missing ISRCs to MP3 files.

    Runs at the start of find_all_divergences. Fixes data issues from
    earlier versions without user interaction.
    """
    from music_manager.services.tagger import strip_youtube_tags, write_isrc  # noqa: PLC0415

    _INT_FIELDS = (
        "track_number",
        "total_tracks",
        "disk_number",
        "total_discs",
        "deezer_id",
        "album_id",
    )
    fixed_types = 0
    fixed_isrc = 0

    for apple_id, entry in tracks.items():
        updates: dict = {}

        # Fix string → int for numeric fields
        for fld in _INT_FIELDS:
            val = entry.get(fld)
            if isinstance(val, str) and val:
                try:
                    updates[fld] = int(val)
                except ValueError:
                    pass

        if updates:
            tracks_store.update(apple_id, updates)
            fixed_types += 1

        # MP3-specific fixes
        lib_entry = library.get(apple_id)
        if not lib_entry:
            continue
        fp = entry.get("file_path") or lib_entry.file_path
        if not fp or not fp.lower().endswith(".mp3"):
            continue

        # Strip YouTube tags that cause album splits
        strip_youtube_tags(fp)

        # Write missing ISRC to MP3 files
        isrc = entry.get("isrc", "")
        if isrc and not lib_entry.isrc:
            if write_isrc(fp, isrc):
                fixed_isrc += 1

    if fixed_types or fixed_isrc:
        tracks_store.save()
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("auto_fix_store", fixed_types=fixed_types, fixed_isrc=fixed_isrc)

    # Detect mixed-format albums (MP3+M4A) → convert MP3 to M4A
    _fix_mixed_format_albums(tracks, tracks_store, library)


def _fix_mixed_format_albums(tracks: dict[str, dict], tracks_store: Tracks, library: dict) -> None:
    """Detect albums with both MP3 and M4A → convert MP3 to M4A.

    Apple Music separates albums by file format. Converting all to M4A
    (Apple's native format) ensures correct grouping.
    Only converts when an album actually has both formats.
    """
    album_formats: dict[int, dict[str, list[tuple[str, str]]]] = {}
    for apple_id, entry in tracks.items():
        album_id = entry.get("album_id")
        if not album_id:
            continue
        lib_e = library.get(apple_id)
        fp = entry.get("file_path") or (lib_e.file_path if lib_e else "")
        if not fp:
            continue
        ext = fp.rsplit(".", 1)[-1].lower() if "." in fp else ""
        if ext in ("mp3", "m4a"):
            album_formats.setdefault(album_id, {}).setdefault(ext, []).append((apple_id, fp))

    # Only convert albums that have BOTH formats
    mp3_to_convert: list[tuple[str, str]] = []
    for formats in album_formats.values():
        if "mp3" in formats and "m4a" in formats:
            mp3_to_convert.extend(formats["mp3"])

    if not mp3_to_convert:
        return

    from music_manager.options.complete_albums import (  # noqa: PLC0415
        _convert_mp3_to_m4a,
    )

    converted = _convert_mp3_to_m4a(mp3_to_convert, tracks_store)
    if converted:
        from music_manager.core.logger import log_event  # noqa: PLC0415

        log_event("fix_mixed_format", converted=converted, total=len(mp3_to_convert))
