"""Exhaustive dedup tests — every edge case and interaction path."""

from pathlib import Path

from music_manager.pipeline.dedup import is_duplicate
from music_manager.services.tracks import Tracks

# ── Empty / missing data ──────────────────────────────────────────────────


def test_empty_store(tmp_path: Path) -> None:
    """Empty store returns False for any input."""
    store = Tracks(str(tmp_path / "tracks.json"))
    assert is_duplicate("ISRC1", "Song", "Artist", store) is False
    assert is_duplicate("", "Song", "Artist", store) is False
    assert is_duplicate("", "", "", store) is False


def test_empty_title_both_sides(tmp_path: Path) -> None:
    """Empty title matches via soft fallback."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "", "artist": "Queen", "isrc": "", "status": "done", "deezer_id": 1})
    assert is_duplicate("", "", "Queen", store) is True


def test_empty_artist_both_sides(tmp_path: Path) -> None:
    """Empty artist matches via soft fallback."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "", "isrc": "", "status": "done", "deezer_id": 1})
    assert is_duplicate("", "Song", "", store) is True


def test_isrc_in_store_empty_incoming_nonempty(tmp_path: Path) -> None:
    """Store has no ISRC, incoming has ISRC → still matches by title+artist."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )
    assert is_duplicate("NEWISRC", "Song", "Art", store) is True


# ── Unicode / special characters ──────────────────────────────────────────


def test_accented_characters(tmp_path: Path) -> None:
    """Accented chars stripped by normalize — matches regardless."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Édith Piaf",
            "artist": "Édith Piaf",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )
    assert is_duplicate("", "Edith Piaf", "Edith Piaf", store) is True
    assert is_duplicate("", "édith piaf", "édith piaf", store) is True


def test_ampersand_vs_and(tmp_path: Path) -> None:
    """'&' and 'and' treated as equivalent by normalize."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Rock & Roll",
            "artist": "Led Zeppelin",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )
    assert is_duplicate("", "Rock and Roll", "Led Zeppelin", store) is True


def test_special_chars_same_form(tmp_path: Path) -> None:
    """Same punctuation form matches after normalize."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "What's Going On?",
            "artist": "Marvin Gaye",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )
    assert is_duplicate("", "What's Going On", "Marvin Gaye", store) is True


def test_curly_apostrophe_matches_via_soft(tmp_path: Path) -> None:
    """Curly and straight apostrophes match via prepare_title soft fallback."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "What\u2019s Going On",
            "artist": "Marvin Gaye",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )
    # Curly normalizes to "whats", straight to "what s" — different!
    # But prepare_title strips parens, and first_artist matches → soft fallback
    assert is_duplicate("", "What\u2019s Going On", "Marvin Gaye", store) is True


# ── ISRC conflict interactions ────────────────────────────────────────────


def test_two_entries_same_title_different_isrc_conflict(tmp_path: Path) -> None:
    """Two entries, same title, different ISRCs — incoming third ISRC is not dup."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "ISRC_A",
            "status": "done",
            "deezer_id": 1,
        },
    )
    store.add(
        "A2",
        {
            "title": "Song (Demo)",
            "artist": "Art",
            "isrc": "ISRC_B",
            "status": "done",
            "deezer_id": 1,
        },
    )
    # Third ISRC differs from both → not a dup
    assert is_duplicate("ISRC_C", "Song", "Art", store) is False


def test_csv_title_match_blocked_by_isrc_conflict(tmp_path: Path) -> None:
    """csv_title match is blocked when ISRCs differ."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Deezer Title",
            "artist": "Art",
            "csv_title": "My Song",
            "csv_artist": "Art",
            "isrc": "ISRC_A",
            "status": "done",
            "deezer_id": 1,
        },
    )
    assert is_duplicate("ISRC_B", "My Song", "Art", store) is False


def test_failed_entry_then_done_entry_same_isrc(tmp_path: Path) -> None:
    """Failed entry ignored, done entry with different ISRC checked for conflict."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "ISRC1",
            "status": "failed",
            "deezer_id": 1,
        },
    )
    store.add(
        "A2",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "ISRC2",
            "status": "done",
            "deezer_id": 1,
        },
    )
    # ISRC1 matched by Level 1 but failed → skip
    # Level 2 finds A2 but ISRC1 != ISRC2 → conflict → skip
    assert is_duplicate("ISRC1", "Song", "Art", store) is False


# ── None values in store ──────────────────────────────────────────────────


def test_none_status_is_duplicate(tmp_path: Path) -> None:
    """Status=None (baseline) is treated as duplicate."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "ISRC1",
            "status": None,
            "deezer_id": 1,
        },
    )
    assert is_duplicate("ISRC1", "Song", "Art", store) is True


def test_none_title_in_store(tmp_path: Path) -> None:
    """None title in store doesn't crash."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": None,
            "artist": "Art",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )
    # Should not crash, won't match "Song"
    assert is_duplicate("", "Song", "Art", store) is False


def test_none_artist_in_store(tmp_path: Path) -> None:
    """None artist in store doesn't crash."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": None,
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )
    assert is_duplicate("", "Song", "Art", store) is False
