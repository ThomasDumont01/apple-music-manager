"""Tests for pipeline/dedup.py — all real-world edge cases encountered."""

from pathlib import Path

from music_manager.pipeline.dedup import is_duplicate
from music_manager.services.tracks import Tracks

# ── Basic dedup ────────────────────────────────────────────────────────────


def test_duplicate_by_isrc(tmp_path: Path) -> None:
    """Track with same ISRC and status done is a duplicate."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "ISRC123",
            "status": "done",
            "deezer_id": 1,
        },
    )

    assert is_duplicate("ISRC123", "Song", "Artist", store) is True
    assert is_duplicate("OTHER", "Different Song", "Other Artist", store) is False


def test_duplicate_by_title_artist(tmp_path: Path) -> None:
    """Track with same normalized title+artist and status done is a duplicate."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )

    assert is_duplicate("", "Bohemian Rhapsody", "Queen", store) is True
    assert is_duplicate("", "bohemian rhapsody", "queen", store) is True
    assert is_duplicate("", "Another Song", "Queen", store) is False


def test_baseline_tracks_are_duplicates(tmp_path: Path) -> None:
    """Baseline tracks (status=None) must be detected as duplicates."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
            "isrc": "GBUM71029604",
            "status": None,
            "deezer_id": 1,
            "origin": "baseline",
        },
    )

    assert is_duplicate("GBUM71029604", "Bohemian Rhapsody", "Queen", store) is True
    assert is_duplicate("", "Bohemian Rhapsody", "Queen", store) is True


def test_not_duplicate_if_failed(tmp_path: Path) -> None:
    """Track with status failed is NOT a duplicate (can be retried)."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Artist",
            "isrc": "ISRC123",
            "status": "failed",
            "deezer_id": 1,
        },
    )

    assert is_duplicate("ISRC123", "Song", "Artist", store) is False
    assert is_duplicate("", "Song", "Artist", store) is False


# ── Soft fallback (real problems encountered) ─────────────────────────────


def test_title_with_movie_context(tmp_path: Path) -> None:
    """Apple Music adds movie context in parens — soft match handles it.

    Real case: "Prince Ali" vs "Prince Ali (De Aladdin/Bande Originale...)"
    """
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": 'Prince Ali (De "Aladdin"/Bande Originale Française du Film)',
            "artist": "Richard Darbois",
            "isrc": "",
            "status": None,
            "deezer_id": 1,
        },
    )

    assert is_duplicate("", "Prince Ali", "Richard Darbois", store) is True


def test_multi_artist_csv_vs_single_library(tmp_path: Path) -> None:
    """CSV has multi-artist, library has primary only.

    Real case: "Lebo M., Jimmy Cliff" vs "Lebo M."
    """
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Hakuna Matata",
            "artist": "Lebo M.",
            "isrc": "",
            "status": None,
            "deezer_id": 1,
        },
    )

    assert is_duplicate("", "Hakuna Matata", "Lebo M., Jimmy Cliff", store) is True


def test_same_song_different_edition(tmp_path: Path) -> None:
    """Same song, different album edition — IS a duplicate.

    Real case: "Thriller" and "Thriller (Deluxe Edition)" are the same song.
    """
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Thriller",
            "artist": "Michael Jackson",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )

    assert is_duplicate("", "Thriller (Deluxe Edition)", "Michael Jackson", store) is True


def test_different_songs_not_duplicates(tmp_path: Path) -> None:
    """Different base titles are never duplicates."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Thriller",
            "artist": "Michael Jackson",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )

    assert is_duplicate("", "Beat It", "Michael Jackson", store) is False
    assert is_duplicate("", "Billie Jean (Demo)", "Michael Jackson", store) is False


# ── ISRC mismatch (different recordings) ──────────────────────────────────


def test_different_isrc_same_csv_title_is_duplicate(tmp_path: Path) -> None:
    """Different ISRCs but same csv_title = same CSV entry already processed.

    Real case: Spotify ISRC differs from Deezer ISRC for the same track.
    csv_title match proves the track was already imported from the same CSV entry.
    """
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Dog Days Are Over (Demo)",
            "artist": "Florence + The Machine",
            "isrc": "GBUM70905782",
            "status": "done",
            "deezer_id": 1,
            "csv_title": "Dog Days Are Over",
            "csv_artist": "Florence + The Machine",
        },
    )

    # Different ISRC but csv_title matches → already processed
    assert (
        is_duplicate("GBUM70900209", "Dog Days Are Over", "Florence + The Machine", store) is True
    )


def test_different_isrc_different_csv_title_not_duplicate(tmp_path: Path) -> None:
    """Different ISRCs AND different csv_title = genuinely different recording."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Dog Days Are Over (Demo)",
            "artist": "Florence + The Machine",
            "isrc": "GBUM70905782",
            "status": "done",
            "deezer_id": 1,
            "csv_title": "Dog Days Are Over (Demo)",
            "csv_artist": "Florence + The Machine",
        },
    )

    # Different ISRC AND different csv_title → genuinely different
    assert (
        is_duplicate("GBUM70900209", "Dog Days Are Over", "Florence + The Machine", store) is False
    )


def test_same_isrc_always_duplicate(tmp_path: Path) -> None:
    """Same ISRC = same recording, always duplicate regardless of title."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Dog Days Are Over (Demo)",
            "artist": "Florence + The Machine",
            "isrc": "GBUM70905782",
            "status": "done",
            "deezer_id": 1,
        },
    )

    assert (
        is_duplicate("GBUM70905782", "Dog Days Are Over", "Florence + The Machine", store) is True
    )


def test_no_isrc_soft_match_still_works(tmp_path: Path) -> None:
    """Without ISRC, soft fallback matches Demo vs non-Demo.

    When neither side has ISRC, we can't distinguish recordings.
    """
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Dog Days Are Over (Demo)",
            "artist": "Florence + The Machine",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )

    assert is_duplicate("", "Dog Days Are Over", "Florence + The Machine", store) is True


# ── CSV title matching ────────────────────────────────────────────────────


def test_csv_title_match(tmp_path: Path) -> None:
    """Match against stored csv_title from previous import."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Deezer Title Variant",
            "artist": "Artist",
            "csv_title": "Original CSV Title",
            "csv_artist": "Artist",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )

    assert is_duplicate("", "Original CSV Title", "Artist", store) is True
    assert is_duplicate("", "Deezer Title Variant", "Artist", store) is True
    assert is_duplicate("", "Unrelated Title", "Artist", store) is False


# ── Title format variants ─────────────────────────────────────────────────


def test_dash_vs_parens_in_title(tmp_path: Path) -> None:
    """Dash and parens variants should match.

    Real case: "Be Your Man - Acoustic" vs "Be Your Man (Acoustic)"
    normalize() strips both to "be your man acoustic".
    """
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add(
        "A1",
        {
            "title": "Be Your Man (Acoustic)",
            "artist": "Rhys Lewis",
            "isrc": "",
            "status": "done",
            "deezer_id": 1,
        },
    )

    assert is_duplicate("", "Be Your Man - Acoustic", "Rhys Lewis", store) is True
