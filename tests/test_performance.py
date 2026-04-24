"""Performance benchmarks — verify logic scales on 5000+ tracks."""

import time
from pathlib import Path

import pytest

from music_manager.core.normalize import first_artist, is_match, normalize
from music_manager.options.find_duplicates import find_duplicates
from music_manager.options.snapshot import snapshot
from music_manager.services.tracks import Tracks

# ── Fixtures ────────────────────────────────────────────────────────────────

_ARTISTS = [
    "Queen",
    "The Beatles",
    "Pink Floyd",
    "Led Zeppelin",
    "AC/DC",
    "Nirvana",
    "Radiohead",
    "Daft Punk",
    "Stromae",
    "Édith Piaf",
    "Bob Marley",
    "David Bowie",
    "Aretha Franklin",
    "Miles Davis",
    "Johnny Hallyday",
    "Charles Aznavour",
    "Serge Gainsbourg",
    "Massive Attack",
    "Gorillaz",
    "Björk",
]

_ALBUMS = [
    "Greatest Hits",
    "Live at Wembley",
    "Remastered",
    "The Collection",
    "Unplugged",
    "Essentials",
    "Deluxe Edition",
    "Best Of",
    "Complete Works",
    "Anthology",
]

_GENRES = ["Rock", "Pop", "Jazz", "Classical", "Hip-Hop", "Electronic", "R&B", "Folk"]


def _generate_tracks(count: int) -> dict[str, dict]:
    """Generate synthetic track entries for performance testing."""
    tracks: dict[str, dict] = {}
    for i in range(count):
        artist = _ARTISTS[i % len(_ARTISTS)]
        album = f"{_ALBUMS[i % len(_ALBUMS)]} {i // 100 + 1}"
        has_isrc = i % 3 != 0  # ~66% have ISRC
        has_deezer = i % 4 != 0  # ~75% identified

        tracks[f"APPLE_{i:05d}"] = {
            "title": f"Track {i} — {artist} Special",
            "artist": artist,
            "album": album,
            "genre": _GENRES[i % len(_GENRES)],
            "year": str(1960 + i % 60),
            "isrc": f"FAKE{i:08d}" if has_isrc else "",
            "deezer_id": i * 10 if has_deezer else 0,
            "album_id": i // 10,
            "duration": 180 + i % 300,
            "origin": "baseline",
            "status": "done" if i % 5 != 0 else None,
        }
    return tracks


@pytest.fixture()
def large_store(tmp_path: Path) -> Tracks:
    """Create a 5000-track store with synthetic data."""
    store = Tracks(str(tmp_path / "tracks.json"))
    for apple_id, entry in _generate_tracks(5000).items():
        store.add(apple_id, entry)
    return store


# ── Tracks store ────────────────────────────────────────────────────────────


def test_tracks_load_save_performance(tmp_path: Path) -> None:
    """Save then load 5000 tracks completes in under 0.5s each."""
    store = Tracks(str(tmp_path / "tracks.json"))
    for apple_id, entry in _generate_tracks(5000).items():
        store.add(apple_id, entry)

    # Save
    start = time.perf_counter()
    store.save()
    save_elapsed = time.perf_counter() - start
    assert save_elapsed < 0.5, f"Saving 5000 tracks took {save_elapsed:.3f}s"

    # Load
    start = time.perf_counter()
    loaded = Tracks(str(tmp_path / "tracks.json"))
    load_elapsed = time.perf_counter() - start
    assert len(loaded.all()) == 5000
    assert load_elapsed < 0.5, f"Loading 5000 tracks took {load_elapsed:.3f}s"


# ── Normalize ───────────────────────────────────────────────────────────────


def test_normalize_batch_performance(large_store: Tracks) -> None:
    """Normalizing 5000 titles + artists + albums completes in under 0.2s."""
    tracks = large_store.all()

    start = time.perf_counter()
    for entry in tracks.values():
        normalize(entry.get("title", ""))
        normalize(entry.get("artist", ""))
        normalize(entry.get("album", ""))
    elapsed = time.perf_counter() - start

    assert elapsed < 0.2, f"Normalizing {len(tracks)} entries took {elapsed:.3f}s"


def test_first_artist_batch_performance(large_store: Tracks) -> None:
    """Extracting first artist from 5000 entries completes in under 0.1s."""
    tracks = large_store.all()

    start = time.perf_counter()
    for entry in tracks.values():
        first_artist(entry.get("artist", ""))
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1, f"first_artist on {len(tracks)} entries took {elapsed:.3f}s"


# ── Fuzzy matching ──────────────────────────────────────────────────────────


def test_is_match_performance() -> None:
    """1000 fuzzy match comparisons complete in under 0.5s."""
    pairs = [
        ("Bohemian Rhapsody", "Bohemian Rhapsody (Remastered)"),
        ("Imagine", "Imagine (Remastered 2010)"),
        ("Hotel California", "Hotel California (Live)"),
        ("Stairway to Heaven", "Stairway To Heaven"),
        ("Smells Like Teen Spirit", "Smells Like Teen Spirit"),
    ] * 200  # 1000 comparisons

    start = time.perf_counter()
    for a, b in pairs:
        is_match(a, b, "title")
    elapsed = time.perf_counter() - start

    assert elapsed < 0.5, f"1000 is_match calls took {elapsed:.3f}s"


# ── Find duplicates ────────────────────────────────────────────────────────


def test_find_duplicates_performance(large_store: Tracks) -> None:
    """find_duplicates on 5000 tracks completes in under 0.5s."""
    start = time.perf_counter()
    find_duplicates(large_store)
    elapsed = time.perf_counter() - start

    count = len(large_store.all())
    assert elapsed < 0.5, f"find_duplicates on {count} tracks took {elapsed:.3f}s"


# ── Snapshot ────────────────────────────────────────────────────────────────


def test_snapshot_performance(large_store: Tracks) -> None:
    """snapshot on 5000 tracks completes in under 0.1s."""
    for i, entry in enumerate(large_store.all().values()):
        if i % 3 == 0:
            entry["origin"] = "imported"
            entry["status"] = "done"

    start = time.perf_counter()
    count = snapshot(large_store)
    elapsed = time.perf_counter() - start

    assert count > 0
    assert elapsed < 0.1, f"snapshot on {len(large_store.all())} tracks took {elapsed:.3f}s"


# ── ISRC index ──────────────────────────────────────────────────────────────


def test_isrc_lookup_performance(large_store: Tracks) -> None:
    """ISRC lookups on 5000-track store are near-instant."""
    tracks = large_store.all()
    isrcs = [e.get("isrc", "") for e in tracks.values() if e.get("isrc")]

    start = time.perf_counter()
    for isrc in isrcs:
        large_store.get_by_isrc(isrc)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1, f"{len(isrcs)} ISRC lookups took {elapsed:.3f}s"
