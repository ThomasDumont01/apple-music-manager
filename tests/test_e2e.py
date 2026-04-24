"""End-to-end tests — full user scenarios across all modules.

Each test simulates a complete user journey: CSV → import → dedup → playlist,
modify → store → dedup, fix-metadata → corrections → verify.
No real API calls — all external services are mocked.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from music_manager.core.config import Paths
from music_manager.core.io import load_csv, save_csv
from music_manager.core.models import LibraryEntry, PendingTrack, Track
from music_manager.options.fix_metadata import apply_corrections, find_all_divergences
from music_manager.options.import_tracks import process_csv
from music_manager.options.modify_track import (
    change_edition,
    edit_metadata_track,
    search_library,
)
from music_manager.pipeline.dedup import is_duplicate
from music_manager.services.albums import Albums
from music_manager.services.resolver import ResolveResult
from music_manager.services.tracks import Tracks
from music_manager.ui.app import MusicApp

_P_IMPORT = "music_manager.options.import_tracks"
_P_IMPORTER = "music_manager.pipeline.importer"
_P_RESOLVER = "music_manager.services.resolver"
_P_APPLE = "music_manager.services.apple"
_P_FIXMETA = "music_manager.options.fix_metadata"


def _paths(tmp_path: Path) -> Paths:
    return Paths(str(tmp_path / "data"))


def _make_track(isrc: str, title: str, artist: str, album: str, **kw) -> Track:
    return Track(
        isrc=isrc,
        title=title,
        artist=artist,
        album=album,
        deezer_id=kw.get("deezer_id", 1),
        album_id=kw.get("album_id", 1),
        **{k: v for k, v in kw.items() if k not in ("deezer_id", "album_id")},
    )


# ══════════════════════════════════════════════════════════════════════════
# E2E 1: Import CSV → all imported → re-import → all skipped
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_P_IMPORT}.log_event")
@patch(f"{_P_IMPORT}.import_resolved_track")
@patch(f"{_P_IMPORT}.resolve")
def test_e2e_import_then_reimport(mock_resolve, mock_import, mock_log, tmp_path) -> None:
    """Full cycle: import 3 tracks, re-import same CSV → all skipped."""
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))
    paths = _paths(tmp_path)

    rows = [
        {"title": "Song A", "artist": "Art1", "album": "Al1", "isrc": "ISRC_A"},
        {"title": "Song B", "artist": "Art2", "album": "Al2", "isrc": "ISRC_B"},
        {"title": "Song C", "artist": "Art3", "album": "Al3", "isrc": "isrc_c"},  # lowercase
    ]
    csv_path = str(tmp_path / "import.csv")
    save_csv(csv_path, rows)

    # Mock: all resolve + import succeed
    call_count = [0]

    def fake_resolve(title, artist, album, isrc, albums_store):
        t = _make_track(isrc.upper(), title, artist, album)
        return ResolveResult("resolved", track=t)

    def fake_import(track, *a, **kw):
        call_count[0] += 1
        track.apple_id = f"AP{call_count[0]}"
        tracks.add(
            track.apple_id,
            {
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "isrc": track.isrc,
                "status": "done",
                "deezer_id": 1,
                "album_id": 1,
            },
        )
        return None

    mock_resolve.side_effect = fake_resolve
    mock_import.side_effect = fake_import

    # First import
    r1 = process_csv(csv_path, paths, tracks, albums)
    assert r1.imported == 3
    assert r1.skipped == 0
    assert len(tracks.all()) == 3

    # Re-create CSV (non-playlist removes imported rows)
    save_csv(csv_path, rows)

    # Second import — all should be skipped (including lowercase ISRC)
    r2 = process_csv(csv_path, paths, tracks, albums)
    assert r2.skipped == 3
    assert r2.imported == 0


# ══════════════════════════════════════════════════════════════════════════
# E2E 2: Import → modify edition → dedup still works
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_P_IMPORTER}.cleanup_covers")
@patch(f"{_P_APPLE}.delete_tracks")
@patch(f"{_P_IMPORTER}.import_resolved_track")
@patch(f"{_P_RESOLVER}.resolve_by_id")
def test_e2e_import_then_change_edition(
    mock_resolve,
    mock_import,
    mock_del,
    mock_clean,
    tmp_path,
) -> None:
    """Import track, change edition → old apple_id gone, new one exists,
    dedup detects the new entry."""
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))

    # Simulate initial import
    tracks.add(
        "OLD_AP",
        {
            "title": "Song",
            "artist": "Art",
            "album": "Al",
            "isrc": "ISRC_OLD",
            "status": "done",
            "deezer_id": 1,
            "album_id": 1,
        },
    )

    # Mock change_edition: resolve new track, import succeeds
    new_track = Track(
        isrc="ISRC_NEW",
        title="Song",
        artist="Art",
        album="Al Deluxe",
        deezer_id=2,
        album_id=2,
    )
    new_track.apple_id = "NEW_AP"
    mock_resolve.return_value = new_track
    mock_import.return_value = None  # success

    # Manually simulate what import_resolved_track does
    def fake_import(track, *a, **kw):
        track.apple_id = "NEW_AP"
        tracks.add(
            "NEW_AP",
            {
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "isrc": track.isrc,
                "status": "done",
                "deezer_id": 2,
                "album_id": 2,
            },
        )
        return None

    mock_import.side_effect = fake_import

    result = change_edition("OLD_AP", 2, _paths(tmp_path), tracks, albums)

    assert result.success is True
    # Old entry removed
    assert tracks.get_by_apple_id("OLD_AP") is None
    assert tracks.get_by_isrc("ISRC_OLD") is None
    # New entry exists
    assert tracks.get_by_apple_id("NEW_AP") is not None
    assert tracks.get_by_isrc("ISRC_NEW") is not None
    # Dedup detects new entry
    assert is_duplicate("ISRC_NEW", "Song", "Art", tracks) is True
    # Old ISRC no longer detected
    assert is_duplicate("ISRC_OLD", "Song", "Art", tracks) is False


# ══════════════════════════════════════════════════════════════════════════
# E2E 3: Import → failed → retry → success
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_P_IMPORT}.log_event")
@patch(f"{_P_IMPORT}.import_resolved_track")
@patch(f"{_P_IMPORT}.resolve")
def test_e2e_import_fail_then_retry_success(
    mock_resolve,
    mock_import,
    mock_log,
    tmp_path,
) -> None:
    """First import fails (YouTube), second attempt succeeds."""
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))
    paths = _paths(tmp_path)

    csv_path = str(tmp_path / "import.csv")
    save_csv(
        csv_path,
        [
            {"title": "Song", "artist": "Art", "album": "Al", "isrc": "ISRC1"},
        ],
    )

    track = _make_track("ISRC1", "Song", "Art", "Al")
    mock_resolve.return_value = ResolveResult("resolved", track=track)

    # First attempt: import fails
    mock_import.return_value = PendingTrack(
        reason="youtube_failed",
        csv_title="Song",
        csv_artist="Art",
    )
    r1 = process_csv(csv_path, paths, tracks, albums)
    assert r1.imported == 0
    assert len(r1.pending) == 1

    # Track is NOT in store as done
    assert not is_duplicate("ISRC1", "Song", "Art", tracks)

    # Re-create CSV for retry
    save_csv(
        csv_path,
        [
            {"title": "Song", "artist": "Art", "album": "Al", "isrc": "ISRC1"},
        ],
    )

    # Second attempt: import succeeds
    def fake_import(trk, *a, **kw):
        trk.apple_id = "AP1"
        tracks.add(
            "AP1",
            {
                "title": "Song",
                "artist": "Art",
                "album": "Al",
                "isrc": "ISRC1",
                "status": "done",
                "deezer_id": 1,
            },
        )
        return None

    mock_import.side_effect = fake_import
    r2 = process_csv(csv_path, paths, tracks, albums)
    assert r2.imported == 1
    assert is_duplicate("ISRC1", "Song", "Art", tracks) is True


# ══════════════════════════════════════════════════════════════════════════
# E2E 4: Playlist import → sync → re-import → already present
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_P_IMPORT}.log_event")
@patch(f"{_P_IMPORT}.add_to_playlist", return_value=2)
@patch(f"{_P_IMPORT}.import_resolved_track")
@patch(f"{_P_IMPORT}.resolve")
def test_e2e_playlist_import_and_sync(
    mock_resolve,
    mock_import,
    mock_pl,
    mock_log,
    tmp_path,
) -> None:
    """Playlist CSV: import tracks, sync playlist, re-import → all skipped."""
    paths = _paths(tmp_path)
    os.makedirs(paths.playlists_dir, exist_ok=True)
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))

    csv_path = str(Path(paths.playlists_dir) / "chill.csv")
    save_csv(
        csv_path,
        [
            {"title": "Song A", "artist": "A1", "album": "Al1", "isrc": "I1"},
            {"title": "Song B", "artist": "A2", "album": "Al2", "isrc": "I2"},
        ],
    )

    call_count = [0]

    def fake_resolve(title, artist, album, isrc, as_):
        return ResolveResult("resolved", track=_make_track(isrc, title, artist, album))

    def fake_import(track, *a, **kw):
        call_count[0] += 1
        aid = f"AP{call_count[0]}"
        track.apple_id = aid
        tracks.add(
            aid,
            {
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "isrc": track.isrc,
                "status": "done",
                "deezer_id": 1,
                "apple_id": aid,
            },
        )
        return None

    mock_resolve.side_effect = fake_resolve
    mock_import.side_effect = fake_import

    # First import
    r1 = process_csv(csv_path, paths, tracks, albums)
    assert r1.imported == 2
    assert r1.playlist_added == 2

    # CSV not cleaned (playlist mode)
    assert len(load_csv(csv_path)) == 2

    # Re-import → skipped + playlist synced
    mock_pl.return_value = 0  # all already in playlist
    r2 = process_csv(csv_path, paths, tracks, albums)
    assert r2.skipped == 2
    assert r2.imported == 0


# ══════════════════════════════════════════════════════════════════════════
# E2E 5: Edit metadata → dedup uses updated title
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_P_APPLE}.update_track")
def test_e2e_edit_metadata_then_dedup(mock_update, tmp_path) -> None:
    """Edit track title → dedup matches new title, not old."""
    tracks = Tracks(str(tmp_path / "t.json"))

    tracks.add(
        "AP1",
        {
            "title": "Old Title",
            "artist": "Artist",
            "album": "Album",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )

    # Edit title
    edit_metadata_track("AP1", {"title": "New Title"}, tracks)

    # Dedup finds new title
    assert is_duplicate("", "New Title", "Artist", tracks) is True
    # Old title no longer matches
    assert is_duplicate("", "Old Title", "Artist", tracks) is False
    # Index is clean
    assert tracks.get_by_title_artist("new title", "artist") is not None
    assert tracks.get_by_title_artist("old title", "artist") is None


# ══════════════════════════════════════════════════════════════════════════
# E2E 6: Fix-metadata → apply → verify fixed
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_P_FIXMETA}.deezer_get")
@patch(f"{_P_FIXMETA}.fetch_album_with_cover")
@patch(f"{_P_FIXMETA}.update_tracks_batch")
def test_e2e_fix_metadata_detect_and_apply(
    mock_apple_update,
    mock_album,
    mock_dz,
    tmp_path,
) -> None:
    """Detect genre divergence → apply correction → re-scan → no divergence."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "AP1",
        {
            "title": "Song",
            "artist": "Art",
            "album": "Album",
            "deezer_id": 123,
            "album_id": 456,
        },
    )
    albums = Albums(str(tmp_path / "a.json"))
    prefs_path = str(tmp_path / "prefs.json")

    album_data = {
        "title": "Album",
        "artist": "Art",
        "album_artist": "Art",
        "genre": "Rock",
        "year": "2020",
        "total_tracks": 1,
        "cover_url": "",
        "release_date": "2020-01-01",
    }
    tracklist = {
        "data": [
            {
                "id": 123,
                "title": "Song",
                "artist": {"name": "Art"},
                "track_position": 1,
                "disk_number": 1,
            }
        ]
    }
    mock_album.return_value = album_data
    mock_dz.return_value = tracklist

    apple = MagicMock()
    apple.get_all.return_value = {
        "AP1": LibraryEntry(
            apple_id="AP1",
            title="Song",
            artist="Art",
            album="Album",
            genre="Pop",
            year="2020",
            track_number=1,
            disk_number=1,
            total_tracks=1,
            album_artist="Art",
            has_artwork=True,
        ),
    }

    # Detect divergences
    divs = find_all_divergences(tracks, albums, apple, prefs_path)
    assert len(divs) == 1
    genre_divs = [d for d in divs[0].divergences if d.field_name == "genre"]
    assert len(genre_divs) == 1
    assert genre_divs[0].deezer_value == "Rock"

    # Apply correction
    count, _ = apply_corrections(genre_divs, tracks)
    assert count == 1

    entry = tracks.get_by_apple_id("AP1")
    assert entry is not None
    assert entry["genre"] == "Rock"

    # Re-scan: update Apple cache to reflect correction
    apple.get_all.return_value["AP1"].genre = "Rock"
    divs2 = find_all_divergences(tracks, albums, apple, prefs_path)
    assert len(divs2) == 0  # no more divergences


# ══════════════════════════════════════════════════════════════════════════
# E2E 7: Auto-sync → import → modify → store consistency
# ══════════════════════════════════════════════════════════════════════════


def test_e2e_auto_sync_then_operations(tmp_path) -> None:
    """Simulate app lifecycle: sync baseline → import → modify → verify."""
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))

    # 1. Auto-sync: Apple has 2 tracks, store is empty
    apple = MagicMock()
    apple.get_all.return_value = {
        "BL1": LibraryEntry(apple_id="BL1", title="Baseline1", artist="Art1", album="Al1"),
        "BL2": LibraryEntry(apple_id="BL2", title="Baseline2", artist="Art2", album="Al2"),
    }

    app = MusicApp(tracks_store=tracks, albums_store=albums, apple=apple)
    app._auto_sync(apple, tracks)

    assert len(tracks.all()) == 2
    bl1 = tracks.get_by_apple_id("BL1")
    assert bl1 is not None
    assert bl1["origin"] == "baseline"

    # 2. Dedup: baseline tracks are NOT detected (not identified)
    assert is_duplicate("", "Baseline1", "Art1", tracks) is False

    # 3. Simulate import: add a new track
    tracks.add(
        "IMP1",
        {
            "title": "Imported",
            "artist": "Art3",
            "album": "Al3",
            "isrc": "ISRC_IMP",
            "status": "done",
            "deezer_id": 1,
            "album_id": 1,
        },
    )

    # 4. Re-sync: Apple now has 3 tracks (2 baseline + 1 imported)
    apple.get_all.return_value["IMP1"] = LibraryEntry(
        apple_id="IMP1",
        title="Imported",
        artist="Art3",
        album="Al3",
    )
    app._auto_sync(apple, tracks)

    # Imported track preserved (not overwritten by baseline)
    entry = tracks.get_by_apple_id("IMP1")
    assert entry is not None
    assert entry["status"] == "done"
    assert entry["deezer_id"] == 1  # enriched data preserved

    # 5. Remove track from Apple → auto-sync removes it
    del apple.get_all.return_value["BL1"]
    app._auto_sync(apple, tracks)
    assert tracks.get_by_apple_id("BL1") is None
    assert len(tracks.all()) == 2  # BL2 + IMP1


# ══════════════════════════════════════════════════════════════════════════
# E2E 8: Store consistency stress — many operations
# ══════════════════════════════════════════════════════════════════════════


def test_e2e_store_consistency_stress(tmp_path) -> None:
    """Many add/update/remove operations → all indexes stay consistent."""
    tracks = Tracks(str(tmp_path / "t.json"))

    # Add 50 tracks
    for i in range(50):
        tracks.add(
            f"A{i}",
            {
                "title": f"Song {i}",
                "artist": f"Artist {i % 10}",
                "album": f"Album {i % 5}",
                "isrc": f"ISRC{i:04d}",
                "csv_title": f"CSV Song {i}",
                "csv_artist": f"Artist {i % 10}",
                "status": "done",
                "deezer_id": 1,
            },
        )

    # Update every 3rd track's title
    for i in range(0, 50, 3):
        tracks.update(f"A{i}", {"title": f"Updated {i}"})

    # Remove every 5th track
    for i in range(0, 50, 5):
        tracks.remove(f"A{i}")

    # Save and reload
    tracks.save()
    reloaded = Tracks(str(tmp_path / "t.json"))

    # Verify consistency
    _ = {aid for aid in reloaded.all()}  # noqa: C416
    for i in range(50):
        aid = f"A{i}"
        was_removed = i % 5 == 0
        was_updated = (i % 3 == 0) and not was_removed

        if was_removed:
            assert reloaded.get_by_apple_id(aid) is None
            assert reloaded.get_by_isrc(f"ISRC{i:04d}") is None
        else:
            entry = reloaded.get_by_apple_id(aid)
            assert entry is not None
            assert reloaded.get_by_isrc(f"ISRC{i:04d}") is not None
            assert reloaded.get_by_isrc(f"isrc{i:04d}") is not None  # case insensitive
            if was_updated:
                assert entry["title"] == f"Updated {i}"


# ══════════════════════════════════════════════════════════════════════════
# E2E 9: Search library → results match store state
# ══════════════════════════════════════════════════════════════════════════


def test_e2e_search_after_operations(tmp_path) -> None:
    """Search reflects current store state after add/remove."""
    tracks = Tracks(str(tmp_path / "t.json"))

    tracks.add(
        "A1",
        {
            "title": "Hello World",
            "artist": "Artist",
            "album": "Album1",
            "deezer_id": 1,
        },
    )
    tracks.add(
        "A2",
        {
            "title": "Hello Again",
            "artist": "Artist",
            "album": "Album1",
            "deezer_id": 1,
        },
    )
    tracks.add("A3", {"title": "Goodbye", "artist": "Other", "album": "Album2", "deezer_id": 1})

    # Search "hello" → 2 tracks
    t, a = search_library("hello", tracks)
    assert len(t) == 2

    # Remove one
    tracks.remove("A1")

    # Search again → 1 track
    t, a = search_library("hello", tracks)
    assert len(t) == 1
    assert t[0].title == "Hello Again"

    # Album search
    t, a = search_library("album1", tracks)
    assert len(a) == 1
    assert a[0].track_count == 1  # A1 removed, only A2 left


# ══════════════════════════════════════════════════════════════════════════
# E2E 10: ISRC case insensitive through entire pipeline
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_P_IMPORT}.log_event")
@patch(f"{_P_IMPORT}.import_resolved_track")
@patch(f"{_P_IMPORT}.resolve")
def test_e2e_isrc_case_through_pipeline(
    mock_resolve,
    mock_import,
    mock_log,
    tmp_path,
) -> None:
    """Lowercase ISRC in CSV → uppercase in store → dedup matches either case."""
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))
    paths = _paths(tmp_path)

    # Import with lowercase ISRC
    csv_path = str(tmp_path / "import.csv")
    save_csv(csv_path, [{"title": "Song", "artist": "Art", "album": "Al", "isrc": "abc123"}])

    def fake_resolve(title, artist, album, isrc, as_):
        return ResolveResult("resolved", track=_make_track(isrc, title, artist, album))

    def fake_import(track, *a, **kw):
        track.apple_id = "AP1"
        tracks.add(
            "AP1",
            {
                "title": "Song",
                "artist": "Art",
                "album": "Al",
                "isrc": track.isrc,
                "status": "done",
                "deezer_id": 1,
            },
        )
        return None

    mock_resolve.side_effect = fake_resolve
    mock_import.side_effect = fake_import

    process_csv(csv_path, paths, tracks, albums)

    # Stored ISRC can be looked up in any case
    assert tracks.get_by_isrc("ABC123") is not None
    assert tracks.get_by_isrc("abc123") is not None
    assert tracks.get_by_isrc("Abc123") is not None

    # Dedup works with any case
    assert is_duplicate("ABC123", "Song", "Art", tracks) is True
    assert is_duplicate("abc123", "Song", "Art", tracks) is True

    # Re-import with uppercase → skipped
    save_csv(csv_path, [{"title": "Song", "artist": "Art", "album": "Al", "isrc": "ABC123"}])
    r = process_csv(csv_path, paths, tracks, albums)
    assert r.skipped == 1
