"""Tests for dedup ISRC case-insensitive handling + edge cases."""

from pathlib import Path

from music_manager.pipeline.dedup import is_duplicate
from music_manager.services.tracks import Tracks

# ── ISRC case-insensitive ─────────────────────────────────────────────────


def test_lowercase_csv_isrc_matches_uppercase_store(tmp_path: Path) -> None:
    """CSV ISRC lowercase matches store ISRC uppercase (Spotify exports)."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Canon de Pachelbel",
            "artist": "Altamirano",
            "isrc": "USHM91068209",
            "status": "done",
            "deezer_id": 1,
        },
    )

    assert is_duplicate("ushm91068209", "Canon de Pachelbel", "Altamirano", store) is True


def test_mixed_case_isrc(tmp_path: Path) -> None:
    """Mixed case ISRC comparison works."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "UsWb12403464",
            "status": "done",
            "deezer_id": 1,
        },
    )

    assert is_duplicate("USWB12403464", "Song", "Artist", store) is True
    assert is_duplicate("uswb12403464", "Song", "Artist", store) is True


# ── ISRC conflict with case difference ────────────────────────────────────


def test_different_isrc_case_insensitive_not_conflict(tmp_path: Path) -> None:
    """Same ISRC different case should NOT be treated as ISRC conflict."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "ABC123",
            "status": "done",
            "deezer_id": 1,
        },
    )

    # Same ISRC, different case → still a duplicate (not conflict)
    assert is_duplicate("abc123", "Song", "Artist", store) is True


def test_different_isrc_same_title_artist_is_duplicate(tmp_path: Path) -> None:
    """Same title+artist with different ISRCs = same song, different edition/territory."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Dog Days Are Over",
            "artist": "Florence + The Machine",
            "isrc": "GBUM70905782",
            "status": "done",
            "deezer_id": 1,
        },
    )

    # Different ISRC but exact title+artist → duplicate (cross-territory ISRC)
    assert (
        is_duplicate("GBUM70900209", "Dog Days Are Over", "Florence + The Machine", store) is True
    )


def test_different_isrc_different_title_not_duplicate(tmp_path: Path) -> None:
    """Different ISRC + different title = genuinely different recording."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Dog Days Are Over",
            "artist": "Florence + The Machine",
            "isrc": "GBUM70905782",
            "status": "done",
            "deezer_id": 1,
        },
    )

    # Different ISRC AND different title → not duplicate
    assert (
        is_duplicate("GBUM70900209", "Dog Days Are Over (Live)", "Florence + The Machine", store)
        is False
    )


# ── Edge cases ────────────────────────────────────────────────────────────


def test_empty_isrc_both_sides(tmp_path: Path) -> None:
    """When both CSV and store have no ISRC, fall back to title matching."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )

    assert is_duplicate("", "Song", "Artist", store) is True
    assert is_duplicate("", "Other", "Artist", store) is False


def test_none_isrc_in_store(tmp_path: Path) -> None:
    """Store entry with None ISRC doesn't crash comparisons."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": None,
            "status": "done",
            "deezer_id": 1,
        },
    )

    # Should not crash, should still match by title+artist
    assert is_duplicate("", "Song", "Artist", store) is True
    assert is_duplicate("SOME_ISRC", "Song", "Artist", store) is True
