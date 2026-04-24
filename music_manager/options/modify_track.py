"""Modify track / album — search library, change edition, cover, metadata.

Used by §11 Modify Track in the menu. All operations work on tracks
already in the library (tracks.json + Apple Music).
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from music_manager.core.config import Paths
from music_manager.core.normalize import normalize
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

# ── Constants ────────────────────────────────────────────────────────────────

_MAX_TRACKS = 10
_MAX_ALBUMS = 5

# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class TrackMatch:
    """A track found in library search."""

    apple_id: str
    title: str
    artist: str
    album: str
    isrc: str
    deezer_id: int


@dataclass
class AlbumMatch:
    """An album found in library search."""

    album_title: str
    artist: str
    track_count: int
    tracks: list[TrackMatch] = field(default_factory=list)


@dataclass
class ModifyResult:
    """Result of a modify operation."""

    success: bool = False
    error: str = ""
    unmatched: list[TrackMatch] = field(default_factory=list)


# ── Entry point: search ──────────────────────────────────────────────────────


def search_library(
    query: str,
    tracks_store: Tracks,
) -> tuple[list[TrackMatch], list[AlbumMatch]]:
    """Filter tracks_store by query. Returns (tracks, albums).

    Scoring (tracks): title starts > title contains > artist starts > artist contains.
    Albums: album name contains query.
    Min 2 characters.
    """
    if len(query) < 2:
        return [], []

    norm_query = normalize(query)
    scored: list[tuple[int, str, dict]] = []
    albums: dict[str, list[tuple[str, dict]]] = {}

    for apple_id, entry in tracks_store.all().items():
        # Only identified tracks
        if not entry.get("deezer_id"):
            continue

        norm_title = normalize(entry.get("title", ""))
        norm_artist = normalize(entry.get("artist", ""))
        album_name = entry.get("album", "")

        # Track scoring
        if norm_title.startswith(norm_query):
            scored.append((0, apple_id, entry))
        elif norm_query in norm_title:
            scored.append((1, apple_id, entry))
        elif norm_artist.startswith(norm_query):
            scored.append((2, apple_id, entry))
        elif norm_query in norm_artist:
            scored.append((3, apple_id, entry))

        # Album grouping
        if album_name and norm_query in normalize(album_name):
            albums.setdefault(album_name, []).append((apple_id, entry))

    # Build track results
    scored.sort(key=lambda x: (x[0], x[2].get("title", "")))
    tracks = [
        TrackMatch(
            apple_id=aid,
            title=e.get("title", ""),
            artist=e.get("artist", ""),
            album=e.get("album", ""),
            isrc=e.get("isrc", ""),
            deezer_id=e.get("deezer_id", 0),
        )
        for _, aid, e in scored[:_MAX_TRACKS]
    ]

    # Build album results
    album_list = []
    for alb_title, entries in sorted(albums.items())[:_MAX_ALBUMS]:
        first = entries[0][1]
        album_list.append(
            AlbumMatch(
                album_title=alb_title,
                artist=first.get("artist", ""),
                track_count=len(entries),
                tracks=[
                    TrackMatch(
                        apple_id=aid,
                        title=e.get("title", ""),
                        artist=e.get("artist", ""),
                        album=alb_title,
                        isrc=e.get("isrc", ""),
                        deezer_id=e.get("deezer_id", 0),
                    )
                    for aid, e in entries
                ],
            )
        )

    return tracks, album_list


# ── Track actions ────────────────────────────────────────────────────────────


def change_edition(
    old_apple_id: str,
    deezer_id: int,
    paths: Paths,
    tracks_store: Tracks,
    albums_store: Albums,
    on_status: Callable[[str], None] | None = None,
) -> ModifyResult:
    """Replace track with a different Deezer edition.

    Downloads new audio via YouTube, imports into Apple Music, deletes old track.
    """
    from music_manager.pipeline.importer import (  # noqa: PLC0415
        cleanup_covers,
        import_resolved_track,
    )
    from music_manager.services.apple import delete_tracks  # noqa: PLC0415
    from music_manager.services.resolver import resolve_by_id  # noqa: PLC0415

    if on_status:
        on_status("resolving")

    track = resolve_by_id(deezer_id, albums_store)
    if not track:
        return ModifyResult(error="deezer_resolve_failed")

    if on_status:
        on_status("importing")

    pending = import_resolved_track(track, paths, tracks_store, albums_store)
    if pending:
        return ModifyResult(error=pending.reason)

    # Delete old track — only if new apple_id differs (Apple Music may return same ID)
    # Remove from store FIRST so crash between steps doesn't leave zombie entry
    new_apple_id = track.apple_id
    if new_apple_id != old_apple_id:
        if on_status:
            on_status("deleting_old")
        tracks_store.remove(old_apple_id)
        delete_tracks([old_apple_id])

    cleanup_covers(paths.tmp_dir)
    tracks_store.save()

    return ModifyResult(success=True)


def redownload_audio(
    apple_id: str,
    tracks_store: Tracks,
    albums_store: Albums,
    paths: Paths,
    on_status: Callable[[str], None] | None = None,
) -> ModifyResult:
    """Re-download audio from YouTube with same ISRC. Replaces file in Apple Music.

    Uses stored ISRC directly (no Deezer API call). Skips duration check
    since the user explicitly wants this track.
    """
    from datetime import datetime  # noqa: PLC0415

    from music_manager.core.models import Track  # noqa: PLC0415
    from music_manager.pipeline.importer import (  # noqa: PLC0415
        cleanup_covers,
        download_cover,
    )
    from music_manager.services.apple import delete_tracks, import_file  # noqa: PLC0415
    from music_manager.services.tagger import tag_audio_file  # noqa: PLC0415
    from music_manager.services.youtube import download_track, search_by_isrc  # noqa: PLC0415

    entry = tracks_store.all().get(apple_id)
    if not entry:
        return ModifyResult(error="track_not_found")

    isrc = entry.get("isrc", "")
    if not isrc:
        return ModifyResult(error="no_isrc")

    # Build Track: use stored data, enrich from Deezer if incomplete
    deezer_id = entry.get("deezer_id", 0) or 0
    if deezer_id and entry.get("album_id"):
        track = Track.from_dict(entry)
    else:
        # Resolve from Deezer via ISRC (single API call)
        from music_manager.services.resolver import (  # noqa: PLC0415
            build_track,
            deezer_get,
            fetch_album_with_cover,
        )

        if on_status:
            on_status("resolving")

        data = deezer_get(f"/track/isrc:{isrc}")
        if data and "error" not in data:
            album_id = data.get("album", {}).get("id", 0)
            album_data = fetch_album_with_cover(album_id, albums_store)
            track = build_track(data, album_data)
        else:
            track = Track.from_dict(entry)

    # YouTube search by stored ISRC
    if on_status:
        on_status("downloading")

    candidates = search_by_isrc(isrc)
    if not candidates:
        return ModifyResult(error="youtube_failed")

    try:
        dl_path, _ = download_track(candidates[0]["url"], paths.tmp_dir)
    except RuntimeError:
        return ModifyResult(error="youtube_download_failed")

    # Cover + Tag
    cover_path = download_cover(track, paths, albums_store)
    tag_audio_file(dl_path, track, cover_path=cover_path)

    # Import into Apple Music
    if on_status:
        on_status("importing")

    try:
        new_apple_id = import_file(dl_path)
    except RuntimeError:
        return ModifyResult(error="import_failed")

    # Update store
    track.apple_id = new_apple_id
    track.status = "done"
    track.origin = "imported"
    track.imported_at = datetime.now().isoformat(timespec="seconds")
    tracks_store.add(new_apple_id, track.to_dict())

    # Delete old — only if new apple_id differs
    # Remove from store FIRST so crash between steps doesn't leave zombie entry
    if new_apple_id != apple_id:
        if on_status:
            on_status("deleting_old")
        tracks_store.remove(apple_id)
        delete_tracks([apple_id])

    tracks_store.save()

    # Cleanup
    import os  # noqa: PLC0415

    if dl_path:
        try:
            os.remove(dl_path)
        except OSError:
            pass
    cleanup_covers(paths.tmp_dir)

    return ModifyResult(success=True)


def replace_audio_url(
    apple_id: str,
    youtube_url: str,
    tracks_store: Tracks,
    albums_store: Albums,
    paths: Paths,
    on_status: Callable[[str], None] | None = None,
) -> ModifyResult:
    """Replace audio with a manual YouTube URL. Keeps same metadata."""
    from music_manager.services.apple import (  # noqa: PLC0415
        delete_tracks,
        import_file,
    )
    from music_manager.services.resolver import (  # noqa: PLC0415
        build_track,
        deezer_get,
        download_cover_file,
        fetch_album_with_cover,
    )
    from music_manager.services.tagger import tag_audio_file  # noqa: PLC0415
    from music_manager.services.youtube import download_track  # noqa: PLC0415

    entry = tracks_store.all().get(apple_id)
    if not entry:
        return ModifyResult(error="track_not_found")

    if on_status:
        on_status("resolving")

    # Build Track: stored data or resolve via ISRC (single API call)
    deezer_id = entry.get("deezer_id", 0) or 0
    isrc = entry.get("isrc", "")
    if deezer_id and entry.get("album_id"):
        from music_manager.core.models import Track  # noqa: PLC0415

        track = Track.from_dict(entry)
    else:
        data = deezer_get(f"/track/isrc:{isrc}") if isrc else None
        if not data or "error" in data:
            data = deezer_get(f"/track/{deezer_id}") if deezer_id else None
        if not data or "error" in data:
            return ModifyResult(error="deezer_resolve_failed")
        album_id = data.get("album", {}).get("id", 0)
        album_data = fetch_album_with_cover(album_id, albums_store)
        track = build_track(data, album_data)

    if on_status:
        on_status("downloading")

    try:
        dl_path, _ = download_track(youtube_url, paths.tmp_dir)
    except RuntimeError:
        return ModifyResult(error="youtube_download_failed")

    # Tag with existing metadata
    cover_path = download_cover_file(track.cover_url, paths.tmp_dir, f"cover_{track.album_id}")
    tag_audio_file(dl_path, track, cover_path=cover_path)

    if on_status:
        on_status("importing")

    try:
        new_apple_id = import_file(dl_path)
    except RuntimeError:
        return ModifyResult(error="import_failed")

    # Update store
    from datetime import datetime  # noqa: PLC0415

    track.apple_id = new_apple_id
    track.status = "done"
    track.origin = "imported"
    track.imported_at = datetime.now().isoformat(timespec="seconds")
    tracks_store.add(new_apple_id, track.to_dict())

    # Delete old — only if new apple_id differs
    if new_apple_id != apple_id:
        if on_status:
            on_status("deleting_old")
        delete_tracks([apple_id])
        tracks_store.remove(apple_id)

    # Cleanup
    import os  # noqa: PLC0415

    for path in (dl_path, cover_path):
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

    return ModifyResult(success=True)


def change_cover_track(
    apple_id: str,
    cover_url: str,
    tracks_store: Tracks,
    paths: Paths,
) -> ModifyResult:
    """Change cover art on a single track."""
    from music_manager.services.apple import set_artwork  # noqa: PLC0415
    from music_manager.services.resolver import download_cover_file  # noqa: PLC0415

    cover_path = download_cover_file(cover_url, paths.tmp_dir, f"cover_modify_{apple_id}")
    if not cover_path:
        return ModifyResult(error="cover_download_failed")

    set_artwork(apple_id, cover_path)

    import os  # noqa: PLC0415

    try:
        os.remove(cover_path)
    except OSError:
        pass

    return ModifyResult(success=True)


def edit_metadata_track(
    apple_id: str,
    fields: dict,
    tracks_store: Tracks,
) -> ModifyResult:
    """Update metadata fields on a track in Apple Music + tracks.json."""
    from music_manager.services.apple import update_track  # noqa: PLC0415

    if not fields:
        return ModifyResult(error="no_fields")

    # Verify track exists before updating
    entry = tracks_store.all().get(apple_id)
    if not entry:
        return ModifyResult(error="track_not_found")

    update_track(apple_id, fields)

    # Update tracks.json via store (sets dirty flag + re-indexes)
    tracks_store.update(apple_id, fields)

    return ModifyResult(success=True)


# ── Album actions ────────────────────────────────────────────────────────────


def change_album_edition(
    album_tracks: list[TrackMatch],
    new_album_id: int,
    paths: Paths,
    tracks_store: Tracks,
    albums_store: Albums,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> ModifyResult:
    """Replace all tracks with the same song from a different album edition.

    Matches by title. Skips tracks that already have the same ISRC.
    """
    from music_manager.core.normalize import normalize, prepare_title  # noqa: PLC0415
    from music_manager.pipeline.importer import (  # noqa: PLC0415
        cleanup_covers,
        import_resolved_track,
    )
    from music_manager.services.apple import delete_tracks  # noqa: PLC0415
    from music_manager.services.resolver import (  # noqa: PLC0415
        build_track,
        deezer_get,
        fetch_album_with_cover,
        get_album_tracklist,
    )

    # Fetch new album data + tracklist (once, outside the loop)
    album_data = fetch_album_with_cover(new_album_id, albums_store)
    tracklist = get_album_tracklist(new_album_id, albums_store)
    if not tracklist:
        return ModifyResult(error="album_tracklist_empty")

    # Pre-build title indexes (normalize + prepare_title for fallback)
    dz_by_norm: dict[str, dict] = {}
    dz_by_prep: dict[str, dict] = {}
    for dz_trk in tracklist:
        dz_title = dz_trk.get("title", "")
        dz_by_norm[normalize(dz_title)] = dz_trk
        dz_by_prep[prepare_title(dz_title)] = dz_trk

    # Match local tracks to new album tracks by title
    total = len(album_tracks)
    imported = 0
    skipped = 0
    unmatched: list[TrackMatch] = []

    for i, local in enumerate(album_tracks):
        if on_progress:
            on_progress(i, total, local.title)

        # Match: exact normalize → prepare_title → startswith (Deezer subtitle)
        norm_local = normalize(local.title)
        dz_match = dz_by_norm.get(norm_local)
        if not dz_match:
            dz_match = dz_by_prep.get(prepare_title(local.title))
        if not dz_match:
            # Fallback: local title is prefix of Deezer title (handles unclosed parens)
            for dz_norm, dz_trk in dz_by_norm.items():
                if dz_norm.startswith(norm_local) and len(norm_local) >= 5:
                    dz_match = dz_trk
                    break
        if not dz_match:
            unmatched.append(local)
            continue

        # Fetch full track data (need ISRC for comparison)
        dz_id = dz_match.get("id", 0)
        full = deezer_get(f"/track/{dz_id}")
        if not full or "error" in full:
            skipped += 1
            continue

        # Same ISRC → same recording, update album metadata in store + Apple Music
        if full.get("isrc", "").upper() == (local.isrc or "").upper():
            from music_manager.services.apple import update_tracks_batch  # noqa: PLC0415

            updates = {
                "album": album_data.get("title", ""),
                "album_id": new_album_id,
                "album_artist": album_data.get("album_artist", ""),
                "genre": album_data.get("genre", ""),
                "release_date": album_data.get("release_date", ""),
                "track_number": full.get("track_position"),
                "total_tracks": album_data.get("total_tracks"),
                "disk_number": full.get("disk_number", 1),
                "total_discs": album_data.get("total_discs", 0),
                "cover_url": album_data.get("cover_url", ""),
            }
            tracks_store.update(local.apple_id, updates)
            # Update Apple Music metadata too
            apple_fields: dict = {}
            if updates.get("album"):
                apple_fields["album"] = updates["album"]
            if updates.get("album_artist"):
                apple_fields["album_artist"] = updates["album_artist"]
            if updates.get("genre"):
                apple_fields["genre"] = updates["genre"]
            if updates.get("track_number") is not None:
                apple_fields["track_number"] = int(updates["track_number"])
            if apple_fields:
                update_tracks_batch({local.apple_id: apple_fields})
            skipped += 1
            continue

        # Different ISRC → delete old first (avoids dedup blocking import)
        tracks_store.remove(local.apple_id)
        delete_tracks([local.apple_id])

        # Import new version
        track = build_track(full, album_data)
        pending = import_resolved_track(track, paths, tracks_store, albums_store)
        if pending:
            skipped += 1
            continue

        imported += 1

    cleanup_covers(paths.tmp_dir)
    tracks_store.save()

    if on_progress:
        on_progress(total, total, "")

    if imported == 0 and skipped == total and not unmatched:
        return ModifyResult(success=True)

    return ModifyResult(success=True, unmatched=unmatched)


def change_cover_album(
    album_tracks: list[TrackMatch],
    cover_url: str,
    paths: Paths,
    on_progress: Callable[[int, int], None] | None = None,
) -> ModifyResult:
    """Change cover art on all tracks in an album."""
    from music_manager.services.apple import set_artwork  # noqa: PLC0415
    from music_manager.services.resolver import download_cover_file  # noqa: PLC0415

    cover_path = download_cover_file(cover_url, paths.tmp_dir, "cover_album_modify")
    if not cover_path:
        return ModifyResult(error="cover_download_failed")

    total = len(album_tracks)
    for i, track in enumerate(album_tracks):
        if on_progress:
            on_progress(i, total)
        set_artwork(track.apple_id, cover_path)

    if on_progress:
        on_progress(total, total)

    import os  # noqa: PLC0415

    try:
        os.remove(cover_path)
    except OSError:
        pass

    return ModifyResult(success=True)


def edit_metadata_album(
    album_tracks: list[TrackMatch],
    fields: dict,
    tracks_store: Tracks,
) -> ModifyResult:
    """Update metadata fields on all tracks in an album."""
    from music_manager.services.apple import update_track  # noqa: PLC0415

    if not fields:
        return ModifyResult(error="no_fields")

    for track in album_tracks:
        update_track(track.apple_id, fields)
        tracks_store.update(track.apple_id, fields)

    return ModifyResult(success=True)


# ── Cover search ─────────────────────────────────────────────────────────────


def search_covers(album_title: str, artist: str) -> list[dict]:
    """Search iTunes for album covers matching album + artist.

    Returns list of dicts: {url, thumbnail, year, track_count, artist, album}.
    Delegates to resolver.search_itunes_covers().
    """
    from music_manager.services.resolver import search_itunes_covers  # noqa: PLC0415

    return search_itunes_covers(album_title, artist)
