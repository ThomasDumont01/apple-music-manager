"""Tests for core/normalize.py."""

from music_manager.core.normalize import first_artist, is_match, match_score, normalize


def test_normalize_strips_accents_and_punctuation() -> None:
    """Accents, punctuation, case → stripped."""
    assert normalize("Rock'n'Roll — Thé & Café") == "rock n roll the and cafe"


def test_is_match_title_typo() -> None:
    """Small typo in title still matches."""
    assert is_match("Bohemian Rapsody", "Bohemian Rhapsody", "title") is True


def test_is_match_artist_article() -> None:
    """'The Beatles' matches 'Beatles' (article is filler)."""
    assert is_match("The Beatles", "Beatles", "artist") is True


def test_is_match_artist_subset_rejected() -> None:
    """'Dave' does NOT match 'Dave Brubeck' (content word difference)."""
    assert is_match("Dave", "Dave Brubeck", "artist") is False


def test_is_match_artist_featuring_stripped() -> None:
    """Featuring suffix is stripped before matching."""
    assert is_match("Daft Punk feat. Pharrell", "Daft Punk", "artist") is True


def test_first_artist_preserves_band_name() -> None:
    """AC/DC stays intact (no space around slash)."""
    assert first_artist("AC/DC") == "AC/DC"
    assert first_artist("Simon & Garfunkel") == "Simon"


# ── match_score ──────────────────────────────────────────────────────────


def test_match_score_identical_title() -> None:
    """Identical titles → score 100."""

    assert match_score("Bohemian Rhapsody", "Bohemian Rhapsody", "title") == 100.0


def test_match_score_similar_title() -> None:
    """Similar titles → high score."""

    score = match_score("Till I Collapse", "Til I Collapse", "title")
    assert score >= 85.0


def test_match_score_different_title() -> None:
    """Different titles → low score."""

    score = match_score("Song A", "Completely Different", "title")
    assert score < 50.0


def test_match_score_artist_identical() -> None:
    """Identical artists → score 100."""

    assert match_score("Queen", "Queen", "artist") == 100.0


def test_match_score_artist_with_article() -> None:
    """Artist with article → high score (filler words ignored)."""

    score = match_score("The Beatles", "Beatles", "artist")
    assert score >= 90.0
