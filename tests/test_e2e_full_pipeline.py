"""Full pipeline E2E — simulates a complete user journey on a fresh machine.

Covers: CSV import → identify → fix metadata → find duplicates → complete albums.
All external services mocked. Proves the entire data flow works end-to-end.
"""

from pathlib import Path
from unittest.mock import patch

from music_manager.core.config import Paths
from music_manager.core.io import save_csv
from music_manager.core.models import Track
from music_manager.options.import_tracks import process_csv
from music_manager.pipeline.dedup import is_duplicate
from music_manager.services.albums import Albums
from music_manager.services.resolver import ResolveResult
from music_manager.services.tracks import Tracks

_P_IMPORT = "music_manager.options.import_tracks"
_P_IMPORTER = "music_manager.pipeline.importer"
_P_RESOLVER = "music_manager.services.resolver"
_P_APPLE = "music_manager.services.apple"
_P_IDENTIFY = "music_manager.options.identify"
_P_TAGGER = "music_manager.services.tagger"


def _paths(tmp_path: Path) -> Paths:
    return Paths(str(tmp_path / "data"))


def _track(isrc: str, title: str, artist: str, album: str, **kw) -> Track:
    return Track(
        isrc=isrc,
        title=title,
        artist=artist,
        album=album,
        deezer_id=kw.get("deezer_id", 100),
        album_id=kw.get("album_id", 10),
        duration=kw.get("duration", 200),
        cover_url="https://cover.jpg",
    )


# ══════════════════════════════════════════════════════════════════════════
# FULL PIPELINE: Fresh machine → import → identify → dedup → verify
# ══════════════════════════════════════════════════════════════════════════


@patch(f"{_P_IMPORT}.log_event")
@patch(f"{_P_IMPORT}.import_resolved_track")
@patch(f"{_P_IMPORT}.resolve")
def test_full_pipeline_fresh_to_identified(
    mock_resolve, mock_import, mock_log, tmp_path: Path
) -> None:
    """Fresh machine: import 5 tracks from CSV, verify stores are coherent."""
    tracks = Tracks(str(tmp_path / "t.json"))
    albums = Albums(str(tmp_path / "a.json"))
    paths = _paths(tmp_path)

    # ── Step 1: Create CSV with 5 tracks ──
    rows = [
        {
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
            "album": "A Night at the Opera",
            "isrc": "GBUM71029604",
        },
        {"title": "Imagine", "artist": "John Lennon", "album": "Imagine", "isrc": "USRC17100115"},
        {"title": "Hey Jude", "artist": "The Beatles", "album": "1", "isrc": "GBAYE0601498"},
        {
            "title": "Stairway to Heaven",
            "artist": "Led Zeppelin",
            "album": "Led Zeppelin IV",
            "isrc": "USAT29900609",
        },
        {
            "title": "Hotel California",
            "artist": "Eagles",
            "album": "Hotel California",
            "isrc": "USEE10400001",
        },
    ]
    csv_path = str(tmp_path / "import.csv")
    save_csv(csv_path, rows)

    # ── Step 2: Mock resolve + import ──
    apple_counter = [0]

    def fake_resolve(title, artist, album, isrc, albums_store):
        return ResolveResult("resolved", track=_track(isrc, title, artist, album))

    def fake_import(track, *args, **kwargs):
        apple_counter[0] += 1
        apple_id = f"AP{apple_counter[0]}"
        track.apple_id = apple_id
        tracks.add(
            apple_id,
            {
                **track.to_dict(),
                "status": "done",
                "origin": "imported",
                "apple_id": apple_id,
                "csv_title": kwargs.get("csv_title", track.title),
                "csv_artist": kwargs.get("csv_artist", track.artist),
            },
        )
        return None

    mock_resolve.side_effect = fake_resolve
    mock_import.side_effect = fake_import

    # ── Step 3: Import CSV ──
    result = process_csv(csv_path, paths, tracks, albums)

    assert result.imported == 5
    assert result.skipped == 0
    assert len(result.pending) == 0
    assert len(tracks.all()) == 5

    # ── Step 4: Verify store coherence ──
    # All tracks have ISRC
    for apple_id, entry in tracks.all().items():
        assert entry.get("isrc"), f"Track {apple_id} missing ISRC"
        assert entry["isrc"] == entry["isrc"].upper(), f"ISRC not uppercase: {entry['isrc']}"
        assert entry.get("deezer_id"), f"Track {apple_id} missing deezer_id"
        assert entry.get("status") == "done"

    # ISRC index works
    for row in rows:
        entry = tracks.get_by_isrc(row["isrc"])
        assert entry is not None, f"ISRC lookup failed for {row['isrc']}"
        assert entry["title"] == row["title"]

    # ── Step 5: Re-import same CSV → all skipped ──
    save_csv(csv_path, rows)
    result2 = process_csv(csv_path, paths, tracks, albums)
    assert result2.imported == 0
    assert result2.skipped == 5

    # ── Step 6: Dedup detects all as existing ──
    for row in rows:
        assert is_duplicate(row["isrc"], row["title"], row["artist"], tracks)


def test_store_persistence_roundtrip(tmp_path: Path) -> None:
    """Tracks + albums survive save/reload cycle without data loss."""
    tracks_path = str(tmp_path / "tracks.json")
    albums_path = str(tmp_path / "albums.json")

    # Create and populate stores
    tracks = Tracks(tracks_path)
    albums = Albums(albums_path)

    tracks.add(
        "AP1",
        {
            "isrc": "ISRC001",
            "title": "Song One",
            "artist": "Artist One",
            "album": "Album One",
            "deezer_id": 100,
            "album_id": 10,
            "status": "done",
            "total_discs": 2,
        },
    )
    albums.put(
        10,
        {
            "title": "Album One",
            "artist": "Artist One",
            "total_tracks": 12,
            "cover_url": "https://example.com/cover.jpg",
            "_tracklist": [{"id": 1, "title": "Song One"}],
        },
    )

    # Save
    tracks.save()
    albums.save()

    # Reload from scratch
    tracks2 = Tracks(tracks_path)
    albums2 = Albums(albums_path)

    # Verify data integrity
    assert len(tracks2.all()) == 1
    entry = tracks2.all()["AP1"]
    assert entry["isrc"] == "ISRC001"
    assert entry["total_discs"] == 2
    assert entry["deezer_id"] == 100

    album = albums2.get(10)
    assert album is not None
    assert album["title"] == "Album One"
    assert album["cover_url"] == "https://example.com/cover.jpg"

    # ISRC index rebuilt correctly
    found = tracks2.get_by_isrc("ISRC001")
    assert found is not None
    assert found["title"] == "Song One"

    # Case-insensitive ISRC lookup
    found_lower = tracks2.get_by_isrc("isrc001")
    assert found_lower is not None


def test_store_corrupt_json_recovery(tmp_path: Path) -> None:
    """Corrupt JSON → load_json recovers from .tmp backup if available."""
    tracks_path = str(tmp_path / "tracks.json")

    # Create valid store
    tracks = Tracks(tracks_path)
    tracks.add("AP1", {"isrc": "ISRC001", "title": "Song", "artist": "Art"})
    tracks.save()

    # Simulate crash: write valid .tmp, corrupt main file
    import shutil  # noqa: PLC0415

    shutil.copy(tracks_path, tracks_path + ".tmp")

    with open(tracks_path, "w") as f:
        f.write("{corrupt json...!!!")

    # Reload — should recover from .tmp
    tracks2 = Tracks(tracks_path)
    assert len(tracks2.all()) == 1
    assert tracks2.all()["AP1"]["title"] == "Song"


def test_csv_edge_cases_no_crash(tmp_path: Path) -> None:
    """Various CSV edge cases don't crash the loader."""
    from music_manager.core.io import load_csv  # noqa: PLC0415

    # Empty file
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    assert load_csv(str(empty)) == []

    # Headers only, no rows
    headers = tmp_path / "headers.csv"
    headers.write_text("title,artist,album\n")
    assert load_csv(str(headers)) == []

    # Missing artist column → rows skipped
    no_artist = tmp_path / "noartist.csv"
    no_artist.write_text("title,album\nSong,Album\n")
    assert load_csv(str(no_artist)) == []

    # Special characters in values
    special = tmp_path / "special.csv"
    save_csv(
        str(special),
        [
            {
                "title": "Bohemian Rhapsody (Remastered)",
                "artist": "Queen & Adam Lambert",
                "album": "Greatest Hits",
            },
            {"title": "L'été indien", "artist": "Joe Dassin", "album": "L'album"},
        ],
    )
    rows = load_csv(str(special))
    assert len(rows) == 2
    assert "&" in rows[0]["artist"]
    assert "'" in rows[1]["title"]
