"""Tests for core/profile.py — user music profile scoring."""

from datetime import UTC, datetime, timedelta

from music_manager.core.profile import (
    LOVED_WEIGHT,
    PLAY_COUNT_CAP,
    PLAY_COUNT_MULTIPLIER,
    build_profile,
)


def _today_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


# ── build_profile ────────────────────────────────────────────────────────────


def test_build_profile_empty_library() -> None:
    """Empty library → empty Profile (no exception)."""
    profile = build_profile({})
    assert profile.top_tracks == []
    assert profile.top_artists == []
    assert profile.top_genres == []
    assert profile.loved_isrcs == set()


def test_build_profile_loved_boost() -> None:
    """A loved track outranks an otherwise identical non-loved track."""
    tracks = {
        "a": {"isrc": "X1", "title": "Song1", "artist": "A1", "loved": True, "play_count": 1},
        "b": {"isrc": "X2", "title": "Song2", "artist": "A2", "loved": False, "play_count": 1},
    }
    profile = build_profile(tracks)
    assert profile.top_tracks[0][0] == "X1"
    assert "X1" in profile.loved_isrcs
    assert "X2" not in profile.loved_isrcs


def test_build_profile_playcount_capped() -> None:
    """play_count contribution is capped — a 1000-listen track can't dominate."""
    tracks = {
        "huge": {"isrc": "X1", "title": "Heavy", "artist": "A1", "play_count": 1000},
        "loved": {"isrc": "X2", "title": "Loved", "artist": "A2", "loved": True, "play_count": 0},
    }
    profile = build_profile(tracks)
    # huge gets PLAY_COUNT_CAP * PLAY_COUNT_MULTIPLIER (40), loved gets LOVED_WEIGHT (50).
    assert PLAY_COUNT_CAP * PLAY_COUNT_MULTIPLIER < LOVED_WEIGHT
    assert profile.top_tracks[0][0] == "X2"


def test_build_profile_genre_filter() -> None:
    """mode='genre:rock' excludes other genres."""
    tracks = {
        "r": {"isrc": "R", "title": "RSong", "artist": "RA", "genre": "Rock", "loved": True},
        "p": {"isrc": "P", "title": "PSong", "artist": "PA", "genre": "Pop", "loved": True},
        "j": {"isrc": "J", "title": "JSong", "artist": "JA", "genre": "Jazz", "loved": True},
    }
    profile = build_profile(tracks, mode="genre:rock")
    assert {isrc for isrc, _, _ in profile.top_tracks} == {"R"}
    assert profile.top_genres == [("Rock", 1)]


def test_build_profile_genre_filter_case_insensitive() -> None:
    """Genre filter matches regardless of case."""
    tracks = {
        "r": {"isrc": "R", "title": "S", "artist": "A", "genre": "ROCK", "loved": True},
    }
    profile = build_profile(tracks, mode="genre:rock")
    assert len(profile.top_tracks) == 1


def test_build_profile_skips_tracks_without_isrc() -> None:
    """ISRC is the universal seed key — entries without it are skipped."""
    tracks = {
        "a": {"isrc": "", "title": "NoIsrc", "artist": "A", "loved": True},
        "b": {"title": "MissingKey", "artist": "B", "loved": True},
        "c": {"isrc": "OK", "title": "Good", "artist": "C", "loved": True},
    }
    profile = build_profile(tracks)
    assert [isrc for isrc, _, _ in profile.top_tracks] == ["OK"]


def test_build_profile_skips_tracks_without_title_or_artist() -> None:
    """Empty title or artist → unusable as a Last.fm seed → skipped."""
    tracks = {
        "a": {"isrc": "X1", "title": "", "artist": "A", "loved": True},
        "b": {"isrc": "X2", "title": "T", "artist": "", "loved": True},
        "c": {"isrc": "X3", "title": "Good", "artist": "Real", "loved": True},
    }
    profile = build_profile(tracks)
    assert [isrc for isrc, _, _ in profile.top_tracks] == ["X3"]


def test_build_profile_isrc_uppercased() -> None:
    """ISRC keys in the profile are normalized to uppercase."""
    tracks = {
        "a": {"isrc": "frabc1234567", "title": "S", "artist": "A", "loved": True},
    }
    profile = build_profile(tracks)
    assert profile.top_tracks[0][0] == "FRABC1234567"
    assert "FRABC1234567" in profile.loved_isrcs


def test_build_profile_recent_add_bonus() -> None:
    """Tracks added within the recency window get a bonus over older identical tracks."""
    old_date = _days_ago(500)
    new_date = _today_iso()
    tracks = {
        "old": {
            "isrc": "OLD", "title": "S1", "artist": "A1",
            "loved": True, "added_date": old_date,
        },
        "new": {
            "isrc": "NEW", "title": "S2", "artist": "A2",
            "loved": True, "added_date": new_date,
        },
    }
    profile = build_profile(tracks)
    assert profile.top_tracks[0][0] == "NEW"


def test_build_profile_unparseable_date_no_crash() -> None:
    """Garbage date strings are silently ignored — no exception."""
    tracks = {
        "x": {
            "isrc": "X1",
            "title": "S",
            "artist": "A",
            "loved": True,
            "added_date": "garbage",
            "last_played": "not-a-date",
        },
    }
    profile = build_profile(tracks)
    assert len(profile.top_tracks) == 1


def test_build_profile_top_artists_aggregated() -> None:
    """Multiple loved tracks by the same artist combine into a single top-artists entry."""
    tracks = {
        "a1": {"isrc": "A1", "title": "S1", "artist": "Same", "loved": True},
        "a2": {"isrc": "A2", "title": "S2", "artist": "Same", "loved": True},
        "b1": {"isrc": "B1", "title": "S3", "artist": "Other", "loved": True},
    }
    profile = build_profile(tracks)
    artists = dict(profile.top_artists)
    assert "Same" in artists and "Other" in artists
    assert artists["Same"] > artists["Other"]


def test_build_profile_zero_score_excluded() -> None:
    """Tracks with no signals at all are excluded (would pollute seeds)."""
    tracks = {
        "neutral": {"isrc": "N1", "title": "S", "artist": "A"},
        "loved": {"isrc": "L1", "title": "S", "artist": "A", "loved": True},
    }
    profile = build_profile(tracks)
    assert [isrc for isrc, _, _ in profile.top_tracks] == ["L1"]


def test_build_profile_mood_mode_uses_full_library() -> None:
    """mode='mood:chill' does not filter by genre — all tracks are scored."""
    tracks = {
        "r": {"isrc": "R", "title": "S", "artist": "A", "genre": "Rock", "loved": True},
        "p": {"isrc": "P", "title": "S2", "artist": "B", "genre": "Pop", "loved": True},
    }
    profile = build_profile(tracks, mode="mood:chill")
    assert len(profile.top_tracks) == 2
