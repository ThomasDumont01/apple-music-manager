"""Tests for pipeline/recommend.py — end-to-end recommendation flow."""

from pathlib import Path
from unittest.mock import patch

import pytest

from music_manager.core.config import Paths
from music_manager.core.models import Track
from music_manager.pipeline import recommend
from music_manager.services import lastfm
from music_manager.services.albums import Albums
from music_manager.services.recommendations_store import RecommendationsStore
from music_manager.services.signals import SignalsLog
from music_manager.services.tracks import Tracks

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _lastfm_clean():
    lastfm._reset_state_for_tests()
    yield
    lastfm._reset_state_for_tests()


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    root = tmp_path / "music"
    root.mkdir()
    return Paths(str(root))


@pytest.fixture
def stores(paths: Paths):
    tracks = Tracks(paths.tracks_path)
    albums = Albums(paths.albums_path)
    recs = RecommendationsStore(paths.recommendations_path)
    return tracks, albums, recs


@pytest.fixture
def with_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LASTFM_API_KEY", "fake-test")


def _seed_loved(tracks: Tracks, isrc: str, title: str, artist: str) -> None:
    tracks.add(
        f"AP_{isrc}",
        {
            "isrc": isrc,
            "title": title,
            "artist": artist,
            "loved": True,
            "play_count": 10,
            "deezer_id": 1,
        },
    )


def _deezer_match(isrc: str, title: str, artist: str, album_id: int = 100) -> dict:
    return {
        "id": 99,
        "title": title,
        "isrc": isrc,
        "duration": 200,
        "explicit_lyrics": False,
        "preview": "",
        "artist": {"name": artist},
        "album": {"id": album_id, "title": "AlbumX"},
    }


# ── scan_outcomes ───────────────────────────────────────────────────────────


def _seed_active(
    recs: RecommendationsStore,
    isrc: str,
    apple_id: str,
    *,
    playlist: str = "library",
    title: str = "T",
    artist: str = "A",
    genre: str = "Rock",
) -> None:
    recs.add_active(
        {
            "isrc": isrc,
            "apple_id": apple_id,
            "title": title,
            "artist": artist,
            "genre": genre,
            "playlist": playlist,
        }
    )


def test_scan_outcomes_no_active_returns_zeros(stores, tmp_path: Path) -> None:
    _, _, recs = stores
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    counts = recommend.scan_outcomes(recs, signals, playlist_name="library")
    assert counts == {"adopted_playlist": 0, "kept_library": 0, "rejected": 0}


def test_scan_outcomes_all_present_no_op(stores, tmp_path: Path) -> None:
    _, _, recs = stores
    _seed_active(recs, "X1", "AP1")
    _seed_active(recs, "X2", "AP2")
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    with (
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks_in_folder",
            return_value=["AP1", "AP2"],
        ),
        patch(
            "music_manager.pipeline.recommend.apple.playlist_exists_in_folder",
            return_value=True,
        ),
    ):
        counts = recommend.scan_outcomes(recs, signals, playlist_name="library")
    assert counts == {"adopted_playlist": 0, "kept_library": 0, "rejected": 0}
    assert signals.count() == 0


def test_scan_outcomes_classifies_adopted_kept_rejected(
    stores, tmp_path: Path
) -> None:
    _, _, recs = stores
    _seed_active(recs, "ADO1", "AP_ADO", artist="AdoptedArt", genre="Rock")
    _seed_active(recs, "KEEP1", "AP_KEEP", artist="KeptArt", genre="Pop")
    _seed_active(recs, "REJ1", "AP_REJ", artist="RejArt", genre="Jazz")
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))

    def fake_membership(apple_id: str):
        if apple_id == "AP_ADO":
            return [("My Favs", "", ["AP_ADO"])]
        return []

    with (
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks_in_folder",
            return_value=[],
        ),
        patch(
            "music_manager.pipeline.recommend.apple.playlist_exists_in_folder",
            return_value=True,
        ),
        patch(
            "music_manager.pipeline.recommend.apple.apple_ids_exist",
            return_value={"AP_ADO", "AP_KEEP"},
        ),
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_membership_detailed",
            side_effect=fake_membership,
        ),
    ):
        counts = recommend.scan_outcomes(recs, signals, playlist_name="library")

    assert counts == {"adopted_playlist": 1, "kept_library": 1, "rejected": 1}
    assert recs.is_adopted("ADO1")
    assert recs.is_kept("KEEP1")
    assert recs.is_rejected("REJ1")
    assert recs.all_outcomes()["ADO1"]["to_playlists"] == ["My Favs"]
    assert recs.all_outcomes()["ADO1"]["genre"] == "Rock"

    event_types = [event["type"] for event in signals.iter_events()]
    assert sorted(event_types) == [
        "recommend_adopted_playlist",
        "recommend_kept_library",
        "recommend_rejected",
    ]


def test_scan_outcomes_inter_for_me_move_classified_as_adopted(
    stores, tmp_path: Path
) -> None:
    """Moving a track from ``for me/library`` to ``for me/rock`` counts as adoption.

    The user is explicitly re-categorising the reco into a different sub-playlist
    of the ``for me`` folder — that's a strong positive signal, not ``kept_library``.
    """
    _, _, recs = stores
    _seed_active(recs, "X1", "AP1", playlist="library")
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))

    def fake_membership(apple_id: str):
        # Track is in ``for me/rock`` (different sub-playlist, same folder).
        return [("rock", "for me", ["AP1"])]

    with (
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks_in_folder",
            return_value=[],
        ),
        patch(
            "music_manager.pipeline.recommend.apple.playlist_exists_in_folder",
            return_value=True,
        ),
        patch(
            "music_manager.pipeline.recommend.apple.apple_ids_exist",
            return_value={"AP1"},
        ),
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_membership_detailed",
            side_effect=fake_membership,
        ),
    ):
        counts = recommend.scan_outcomes(recs, signals, playlist_name="library")

    assert counts["adopted_playlist"] == 1
    assert counts["kept_library"] == 0
    assert recs.all_outcomes()["X1"]["to_playlists"] == ["rock"]


def test_scan_outcomes_filters_by_playlist_field(stores, tmp_path: Path) -> None:
    """Entries belonging to another sub-playlist are not touched."""
    _, _, recs = stores
    _seed_active(recs, "X1", "AP1", playlist="library")
    _seed_active(recs, "Y1", "AP2", playlist="rock")
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    with (
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks_in_folder",
            return_value=[],
        ),
        patch(
            "music_manager.pipeline.recommend.apple.playlist_exists_in_folder",
            return_value=True,
        ),
        patch(
            "music_manager.pipeline.recommend.apple.apple_ids_exist", return_value=set()
        ),
    ):
        counts = recommend.scan_outcomes(recs, signals, playlist_name="library")
    assert counts == {"adopted_playlist": 0, "kept_library": 0, "rejected": 1}
    assert recs.is_rejected("X1")
    assert recs.is_active("Y1")  # untouched


def test_scan_outcomes_missing_playlist_no_classification(
    stores, tmp_path: Path
) -> None:
    """If the playlist is absent from the folder, we DO NOT mass-classify."""
    _, _, recs = stores
    _seed_active(recs, "X1", "AP1")
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    with (
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks_in_folder",
            return_value=[],
        ),
        patch(
            "music_manager.pipeline.recommend.apple.playlist_exists_in_folder",
            return_value=False,
        ),
    ):
        counts = recommend.scan_outcomes(recs, signals, playlist_name="library")
    assert counts == {"adopted_playlist": 0, "kept_library": 0, "rejected": 0}
    assert recs.is_active("X1")
    assert signals.count() == 0


def test_scan_outcomes_apple_failure_returns_zeros(stores, tmp_path: Path) -> None:
    _, _, recs = stores
    _seed_active(recs, "X1", "AP1")
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    with patch(
        "music_manager.pipeline.recommend.apple.get_playlist_tracks_in_folder",
        side_effect=RuntimeError("AppleScript error"),
    ):
        counts = recommend.scan_outcomes(recs, signals, playlist_name="library")
    assert counts == {"adopted_playlist": 0, "kept_library": 0, "rejected": 0}
    assert recs.is_active("X1")


def test_scan_outcomes_entry_without_apple_id_is_skipped(
    stores, tmp_path: Path
) -> None:
    _, _, recs = stores
    recs.add_active(
        {
            "isrc": "ORPHAN",
            "apple_id": "",
            "title": "T",
            "artist": "A",
            "playlist": "library",
        }
    )
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    with (
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks_in_folder",
            return_value=[],
        ),
        patch(
            "music_manager.pipeline.recommend.apple.playlist_exists_in_folder",
            return_value=True,
        ),
    ):
        counts = recommend.scan_outcomes(recs, signals, playlist_name="library")
    assert counts == {"adopted_playlist": 0, "kept_library": 0, "rejected": 0}


# ── _append_unique (mode-aware match filter) ───────────────────────────────


def test_append_unique_default_filters_low_match() -> None:
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    recommend._append_unique(
        candidates, seen,
        {"name": "S", "artist": "A", "match": 0.2},
        source="lastfm_similar", seed_isrc="", mode="library",
    )
    assert candidates == []


def test_append_unique_default_accepts_high_match() -> None:
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    recommend._append_unique(
        candidates, seen,
        {"name": "S", "artist": "A", "match": 0.9},
        source="lastfm_similar", seed_isrc="", mode="library",
    )
    assert len(candidates) == 1


def test_append_unique_discovery_drops_too_low_match() -> None:
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    recommend._append_unique(
        candidates, seen,
        {"name": "S", "artist": "A", "match": 0.3},
        source="lastfm_similar", seed_isrc="", mode="discovery",
    )
    assert candidates == []


def test_append_unique_discovery_drops_too_high_match() -> None:
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    recommend._append_unique(
        candidates, seen,
        {"name": "S", "artist": "A", "match": 0.85},
        source="lastfm_similar", seed_isrc="", mode="discovery",
    )
    assert candidates == []


def test_append_unique_discovery_accepts_mid_match() -> None:
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    recommend._append_unique(
        candidates, seen,
        {"name": "S", "artist": "A", "match": 0.55},
        source="lastfm_similar", seed_isrc="", mode="discovery",
    )
    assert len(candidates) == 1


# ── _apply_discovery_bonuses ───────────────────────────────────────────────


def test_apply_discovery_bonus_cold_artist() -> None:
    candidate = _make_candidate(artist="UnknownArtist", base_score=50.0)
    recommend._apply_discovery_bonuses(candidate, set(), set())
    assert candidate.score == 50.0 + recommend._DISCOVERY_COLD_ARTIST_BONUS


def test_apply_discovery_malus_familiar_artist() -> None:
    candidate = _make_candidate(artist="FamiliarArtist", base_score=50.0)
    recommend._apply_discovery_bonuses(
        candidate, {"familiarartist"}, {"familiarartist"}
    )
    assert candidate.score == 50.0 - recommend._DISCOVERY_FAMILIARITY_MALUS


def test_apply_discovery_known_but_not_top_no_bonus_no_malus() -> None:
    candidate = _make_candidate(artist="Mid", base_score=50.0)
    recommend._apply_discovery_bonuses(candidate, set(), {"mid"})
    assert candidate.score == 50.0


def test_apply_discovery_empty_artist_noop() -> None:
    candidate = _make_candidate(artist="", base_score=50.0)
    recommend._apply_discovery_bonuses(candidate, {"x"}, {"x"})
    assert candidate.score == 50.0


# ── _dedup_and_rank in discovery mode ──────────────────────────────────────


def test_dedup_and_rank_discovery_boosts_cold_artist(stores) -> None:
    tracks, _, recs = stores
    tracks.add(
        "AP1",
        {"isrc": "K1", "title": "T", "artist": "KnownArtist", "loved": True},
    )
    cold = _make_candidate(isrc="C1", artist="NeverSeen", base_score=50.0)
    known = _make_candidate(isrc="K2", artist="KnownArtist", base_score=50.0)
    kept, _ = recommend._dedup_and_rank(
        [cold, known], recommend.Profile(), tracks, recs, signals=None, mode="discovery"
    )
    assert kept[0].isrc == "C1"
    assert kept[0].score > kept[1].score


def test_dedup_and_rank_non_discovery_does_not_apply_cold_bonus(stores) -> None:
    tracks, _, recs = stores
    tracks.add(
        "AP1", {"isrc": "K1", "title": "T", "artist": "KnownArtist", "loved": True}
    )
    cold = _make_candidate(isrc="C1", artist="NeverSeen", base_score=50.0)
    kept, _ = recommend._dedup_and_rank(
        [cold], recommend.Profile(), tracks, recs, signals=None, mode="library"
    )
    assert kept[0].score == 50.0


# ── _apply_affinity ─────────────────────────────────────────────────────────


def _make_candidate(
    *,
    isrc: str = "X1",
    title: str = "Song",
    artist: str = "Artist",
    genre: str = "Rock",
    base_score: float = 50.0,
) -> recommend.RecommendationCandidate:
    track = Track(
        isrc=isrc, title=title, artist=artist, album="Album",
        genre=genre, deezer_id=1, album_id=10,
    )
    return recommend.RecommendationCandidate(
        isrc=isrc,
        deezer_id=1,
        title=title,
        artist=artist,
        track=track,
        source="lastfm_similar",
        seed_isrc="",
        score=base_score,
        match=0.5,
        playcount=0,
    )


def test_apply_affinity_artist_positive_bonus() -> None:
    candidate = _make_candidate(artist="GoodArtist", base_score=50.0)
    recommend._apply_affinity(candidate, {"goodartist": 0.8}, {})
    assert candidate.score == 50.0 + recommend._AFFINITY_ARTIST_BONUS


def test_apply_affinity_artist_negative_malus() -> None:
    candidate = _make_candidate(artist="BadArtist", base_score=50.0)
    recommend._apply_affinity(candidate, {"badartist": -0.6}, {})
    assert candidate.score == 50.0 - recommend._AFFINITY_ARTIST_MALUS


def test_apply_affinity_neutral_no_effect() -> None:
    candidate = _make_candidate(artist="MehArtist", base_score=50.0)
    recommend._apply_affinity(candidate, {"mehartist": 0.1}, {})
    assert candidate.score == 50.0


def test_apply_affinity_genre_positive() -> None:
    candidate = _make_candidate(genre="Pop", base_score=50.0)
    recommend._apply_affinity(candidate, {}, {"pop": 0.9})
    assert candidate.score == 50.0 + recommend._AFFINITY_GENRE_BONUS


def test_apply_affinity_genre_negative() -> None:
    candidate = _make_candidate(genre="Metal", base_score=50.0)
    recommend._apply_affinity(candidate, {}, {"metal": -0.4})
    assert candidate.score == 50.0 - recommend._AFFINITY_GENRE_MALUS


def test_apply_affinity_stacks_artist_and_genre() -> None:
    candidate = _make_candidate(artist="Good", genre="Pop", base_score=50.0)
    recommend._apply_affinity(candidate, {"good": 0.8}, {"pop": 0.8})
    expected = 50.0 + recommend._AFFINITY_ARTIST_BONUS + recommend._AFFINITY_GENRE_BONUS
    assert candidate.score == expected


def test_apply_affinity_no_genre_skips_genre_lookup() -> None:
    candidate = _make_candidate(genre="", base_score=50.0)
    recommend._apply_affinity(candidate, {}, {"": 1.0})
    assert candidate.score == 50.0


# ── _dedup_and_rank with signals ───────────────────────────────────────────


def test_dedup_and_rank_no_signals_works_like_before(stores) -> None:
    tracks, _, recs = stores
    candidate = _make_candidate()
    kept, _counters = recommend._dedup_and_rank(
        [candidate], recommend.Profile(), tracks, recs, signals=None
    )
    assert len(kept) == 1


def test_dedup_and_rank_applies_affinity_when_signals_given(
    stores, tmp_path: Path
) -> None:
    tracks, _, recs = stores
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    for i in range(3):
        signals.log("recommend_rejected", isrc=f"X{i}", artist="BadArt", genre="Rock")
    for i in range(3):
        signals.log(
            "recommend_adopted_playlist", isrc=f"Y{i}", artist="GoodArt", genre="Pop"
        )

    bad = _make_candidate(isrc="BAD1", artist="BadArt", genre="Rock", base_score=50.0)
    good = _make_candidate(isrc="GOOD1", artist="GoodArt", genre="Pop", base_score=50.0)
    kept, _ = recommend._dedup_and_rank(
        [bad, good], recommend.Profile(), tracks, recs, signals=signals
    )
    # GoodArt should sort first (higher score after bonuses)
    assert kept[0].isrc == "GOOD1"
    assert kept[1].isrc == "BAD1"
    assert kept[0].score > kept[1].score


# ── _detect_deltas ──────────────────────────────────────────────────────────


def test_detect_deltas_no_changes_no_signals(stores, tmp_path: Path) -> None:
    tracks, _, recs = stores
    tracks.add(
        "AP1",
        {"isrc": "X1", "title": "T", "artist": "A", "loved": False, "play_count": 0},
    )
    _seed_active(recs, "X1", "AP1")
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    counts = recommend._detect_deltas(recs, tracks, signals)
    assert counts == {"loved": 0, "playcount": 0}
    assert signals.count() == 0


def test_detect_deltas_loved_becomes_true(stores, tmp_path: Path) -> None:
    tracks, _, recs = stores
    tracks.add(
        "AP1",
        {"isrc": "X1", "title": "T", "artist": "A", "loved": True, "play_count": 0},
    )
    _seed_active(recs, "X1", "AP1")  # snapshot defaults to loved=False
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    counts = recommend._detect_deltas(recs, tracks, signals)
    assert counts["loved"] == 1
    events = list(signals.iter_events())
    assert events[0]["type"] == "loved_delta"
    assert events[0]["to_loved"] is True
    assert recs.all_active()["X1"]["last_seen_loved"] is True


def test_detect_deltas_loved_becomes_false(stores, tmp_path: Path) -> None:
    tracks, _, recs = stores
    tracks.add(
        "AP1",
        {"isrc": "X1", "title": "T", "artist": "A", "loved": False, "play_count": 0},
    )
    recs.add_active(
        {
            "isrc": "X1",
            "apple_id": "AP1",
            "title": "T",
            "artist": "A",
            "playlist": "library",
            "last_seen_loved": True,
        }
    )
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    counts = recommend._detect_deltas(recs, tracks, signals)
    assert counts["loved"] == 1
    events = list(signals.iter_events())
    assert events[0]["to_loved"] is False


def test_detect_deltas_playcount_increase(stores, tmp_path: Path) -> None:
    tracks, _, recs = stores
    tracks.add(
        "AP1",
        {"isrc": "X1", "title": "T", "artist": "A", "loved": False, "play_count": 7},
    )
    _seed_active(recs, "X1", "AP1")
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    counts = recommend._detect_deltas(recs, tracks, signals)
    assert counts["playcount"] == 1
    events = list(signals.iter_events())
    assert events[0]["type"] == "playcount_delta"
    assert events[0]["delta"] == 7
    assert events[0]["new_count"] == 7
    assert recs.all_active()["X1"]["last_seen_playcount"] == 7


def test_detect_deltas_playcount_unchanged_no_signal(stores, tmp_path: Path) -> None:
    tracks, _, recs = stores
    tracks.add(
        "AP1",
        {"isrc": "X1", "title": "T", "artist": "A", "loved": False, "play_count": 5},
    )
    recs.add_active(
        {
            "isrc": "X1",
            "apple_id": "AP1",
            "title": "T",
            "artist": "A",
            "playlist": "library",
            "last_seen_playcount": 5,
        }
    )
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    counts = recommend._detect_deltas(recs, tracks, signals)
    assert counts == {"loved": 0, "playcount": 0}
    assert signals.count() == 0


def test_detect_deltas_skips_tracks_not_in_store(stores, tmp_path: Path) -> None:
    _, _, recs = stores
    _seed_active(recs, "X1", "AP_MISSING")
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    tracks, _, _ = stores
    counts = recommend._detect_deltas(recs, tracks, signals)
    assert counts == {"loved": 0, "playcount": 0}


def test_detect_deltas_skips_entries_without_apple_id(stores, tmp_path: Path) -> None:
    tracks, _, recs = stores
    recs.add_active(
        {
            "isrc": "X1",
            "apple_id": "",
            "title": "T",
            "artist": "A",
            "playlist": "library",
        }
    )
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    counts = recommend._detect_deltas(recs, tracks, signals)
    assert counts == {"loved": 0, "playcount": 0}


def test_detect_deltas_ignores_outcomes_section(stores, tmp_path: Path) -> None:
    """Tracks already moved to outcomes don't trigger fake deltas."""
    tracks, _, recs = stores
    tracks.add(
        "AP1",
        {"isrc": "X1", "title": "T", "artist": "A", "loved": True, "play_count": 9},
    )
    # Track went straight to outcomes (never active) — record_outcome with
    # state=rejected. _detect_deltas should not even see it.
    recs.record_outcome("X1", state="rejected", title="T", artist="A")
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    counts = recommend._detect_deltas(recs, tracks, signals)
    assert counts == {"loved": 0, "playcount": 0}
    assert signals.count() == 0


def test_detect_deltas_loved_and_playcount_both_change(
    stores, tmp_path: Path
) -> None:
    tracks, _, recs = stores
    tracks.add(
        "AP1",
        {"isrc": "X1", "title": "T", "artist": "A", "loved": True, "play_count": 4},
    )
    _seed_active(recs, "X1", "AP1")
    signals = SignalsLog(str(tmp_path / "signals.jsonl"))
    counts = recommend._detect_deltas(recs, tracks, signals)
    assert counts == {"loved": 1, "playcount": 1}
    snapshot = recs.all_active()["X1"]
    assert snapshot["last_seen_loved"] is True
    assert snapshot["last_seen_playcount"] == 4


# ── playlist_name_for_mode ─────────────────────────────────────────────────


def testplaylist_name_for_mode_library() -> None:
    assert recommend.playlist_name_for_mode("library") == "library"


def testplaylist_name_for_mode_general_aliases_library() -> None:
    """``general`` is the legacy name for ``library``."""
    assert recommend.playlist_name_for_mode("general") == "library"


def testplaylist_name_for_mode_discovery() -> None:
    assert recommend.playlist_name_for_mode("discovery") == "discovery"


def testplaylist_name_for_mode_genre_lowercase() -> None:
    assert recommend.playlist_name_for_mode("genre:Rock") == "rock"


def testplaylist_name_for_mode_genre_whitespace_to_dash() -> None:
    assert recommend.playlist_name_for_mode("genre:Indie Rock") == "indie-rock"


def testplaylist_name_for_mode_genre_hyphen_preserved() -> None:
    assert recommend.playlist_name_for_mode("genre:Hip-Hop") == "hip-hop"


def testplaylist_name_for_mode_playlist_strips_accents() -> None:
    assert recommend.playlist_name_for_mode("playlist:Mes Favoris") == "mes-favoris"
    assert recommend.playlist_name_for_mode("playlist:Été") == "ete"


def testplaylist_name_for_mode_mood() -> None:
    assert recommend.playlist_name_for_mode("mood:Chill") == "chill"


def testplaylist_name_for_mode_strips_quotes_and_slashes() -> None:
    """Characters that confuse AppleScript or look like path separators are scrubbed."""
    assert recommend.playlist_name_for_mode('playlist:My "Best"') == "my-best"
    assert recommend.playlist_name_for_mode("playlist:Rock/Pop") == "rock-pop"


def testplaylist_name_for_mode_truncates_to_max_length() -> None:
    long_value = "a" * 200
    name = recommend.playlist_name_for_mode(f"playlist:{long_value}")
    assert len(name) <= 50


def testplaylist_name_for_mode_invalid_mode_raises() -> None:
    with pytest.raises(ValueError):
        recommend.playlist_name_for_mode("nonsense")
    with pytest.raises(ValueError):
        recommend.playlist_name_for_mode("")


def testplaylist_name_for_mode_empty_value_after_prefix_raises() -> None:
    with pytest.raises(ValueError):
        recommend.playlist_name_for_mode("genre:")
    with pytest.raises(ValueError):
        recommend.playlist_name_for_mode("playlist:   ")


def testplaylist_name_for_mode_unknown_prefix_raises() -> None:
    with pytest.raises(ValueError):
        recommend.playlist_name_for_mode("foo:bar")


# ── generate_recommendations ────────────────────────────────────────────────


def test_generate_no_api_key_returns_error(stores, paths) -> None:
    """Missing API key short-circuits with a clean error code."""
    tracks, albums, recs = stores
    with patch.dict("os.environ", clear=True):
        with patch(
            "music_manager.services.lastfm.load_config", return_value={"lastfm_api_key": ""}
        ):
            result = recommend.generate_recommendations(
                mode="general",
                paths=paths,
                tracks_store=tracks,
                albums_store=albums,
                recs_store=recs,
            )
    assert result.error == "lastfm_no_api_key"
    assert result.imported == 0


def test_generate_handles_lastfm_empty(stores, paths, with_api_key) -> None:
    """Last.fm returns nothing → graceful no-op."""
    tracks, albums, recs = stores
    _seed_loved(tracks, "SEED1", "Song", "Artist")
    with (
        patch("music_manager.services.lastfm.get_similar_tracks", return_value=[]),
        patch("music_manager.services.lastfm.get_top_tracks_by_tag", return_value=[]),
        patch("music_manager.services.lastfm.get_similar_artists", return_value=[]),
    ):
        result = recommend.generate_recommendations(
            mode="general",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
        )
    assert result.imported == 0
    assert result.error == "lastfm_empty"


def test_generate_dedup_skips_blacklist(stores, paths, with_api_key) -> None:
    """Blacklisted ISRC must never reach import_resolved_track. CRITICAL."""
    tracks, albums, recs = stores
    _seed_loved(tracks, "SEED1", "Song", "Artist")
    recs.blacklist("REC1", title="Banned", artist="Bad")

    similar = [{"name": "BannedTrack", "artist": "Bad", "mbid": "", "match": 0.9}]
    deezer = [_deezer_match("REC1", "BannedTrack", "Bad")]

    import_calls: list[Track] = []

    def fake_import(track, *_args, **_kwargs):
        import_calls.append(track)
        return None

    with (
        patch("music_manager.services.lastfm.get_similar_tracks", return_value=similar),
        patch("music_manager.pipeline.recommend.search_track", return_value=deezer),
        patch(
            "music_manager.pipeline.recommend.fetch_album_with_cover",
            return_value={"id": 100, "title": "AlbumX", "cover_url": ""},
        ),
        patch(
            "music_manager.pipeline.recommend.import_resolved_track", side_effect=fake_import
        ),
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks", return_value=[]
        ),
        patch("music_manager.pipeline.recommend.apple.list_playlists", return_value=[]),
        patch("music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"),
    ):
        result = recommend.generate_recommendations(
            mode="general",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
        )

    assert import_calls == []
    assert result.imported == 0
    assert result.skipped_blacklist == 1


def test_generate_dedup_skips_active(stores, paths, with_api_key) -> None:
    """An ISRC already in active does not get reproposed."""
    tracks, albums, recs = stores
    _seed_loved(tracks, "SEED1", "Song", "Artist")
    recs.add_active({"isrc": "REC1", "apple_id": "AP_OLD", "title": "Old", "artist": "A"})

    similar = [{"name": "T", "artist": "A", "mbid": "", "match": 0.9}]
    deezer = [_deezer_match("REC1", "T", "A")]

    with (
        patch("music_manager.services.lastfm.get_similar_tracks", return_value=similar),
        patch("music_manager.pipeline.recommend.search_track", return_value=deezer),
        patch(
            "music_manager.pipeline.recommend.fetch_album_with_cover",
            return_value={"id": 100, "title": "AlbumX", "cover_url": ""},
        ),
        patch(
            "music_manager.pipeline.recommend.import_resolved_track", return_value=None
        ) as mock_import,
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks",
            return_value=["AP_OLD"],
        ),
        patch("music_manager.pipeline.recommend.apple.list_playlists", return_value=[]),
        patch("music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"),
    ):
        result = recommend.generate_recommendations(
            mode="general",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
        )
    mock_import.assert_not_called()
    assert result.skipped_already_active == 1


def test_generate_dedup_skips_library(stores, paths, with_api_key) -> None:
    """An ISRC already identified in the library is skipped."""
    tracks, albums, recs = stores
    # Seed loved (drives Last.fm seeds).
    _seed_loved(tracks, "SEED1", "Song", "Artist")
    # Plant the recommendation ISRC as already in library (deezer_id set → "identified").
    tracks.add(
        "AP_LIB",
        {
            "isrc": "REC1",
            "title": "Existing",
            "artist": "InLib",
            "deezer_id": 42,
            "status": "done",
        },
    )

    similar = [{"name": "Existing", "artist": "InLib", "mbid": "", "match": 0.9}]
    deezer = [_deezer_match("REC1", "Existing", "InLib")]

    with (
        patch("music_manager.services.lastfm.get_similar_tracks", return_value=similar),
        patch("music_manager.pipeline.recommend.search_track", return_value=deezer),
        patch(
            "music_manager.pipeline.recommend.fetch_album_with_cover",
            return_value={"id": 100, "title": "AlbumX", "cover_url": ""},
        ),
        patch(
            "music_manager.pipeline.recommend.import_resolved_track", return_value=None
        ) as mock_import,
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks", return_value=[]
        ),
        patch("music_manager.pipeline.recommend.apple.list_playlists", return_value=[]),
        patch("music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"),
    ):
        result = recommend.generate_recommendations(
            mode="general",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
        )
    mock_import.assert_not_called()
    assert result.skipped_in_library == 1


def test_generate_ranks_top_target_count(stores, paths, with_api_key) -> None:
    """Only ``target_count`` candidates are imported, sorted by score."""
    tracks, albums, recs = stores
    _seed_loved(tracks, "SEED1", "Song", "Artist")

    similar = [
        {"name": f"T{i}", "artist": f"Art{i}", "mbid": "", "match": i / 100.0}
        for i in range(50)
    ]
    deezer_results = {
        f"T{i}_Art{i}": [_deezer_match(f"REC{i:03d}", f"T{i}", f"Art{i}")] for i in range(50)
    }

    def fake_search(name: str, artist: str) -> list[dict]:
        return deezer_results.get(f"{name}_{artist}", [])

    import_calls: list[Track] = []

    def fake_import(track: Track, *_args, **_kwargs):
        track.apple_id = f"AP_{track.isrc}"
        import_calls.append(track)
        return None

    with (
        patch("music_manager.services.lastfm.get_similar_tracks", return_value=similar),
        patch("music_manager.pipeline.recommend.search_track", side_effect=fake_search),
        patch(
            "music_manager.pipeline.recommend.fetch_album_with_cover",
            return_value={"id": 100, "title": "AlbumX", "cover_url": ""},
        ),
        patch(
            "music_manager.pipeline.recommend.import_resolved_track", side_effect=fake_import
        ),
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks", return_value=[]
        ),
        patch("music_manager.pipeline.recommend.apple.list_playlists", return_value=[]),
        patch("music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"),
    ):
        result = recommend.generate_recommendations(
            mode="general",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
            target_count=20,
        )

    assert result.imported == 20
    # Top-ranked candidate has match=0.49 → 49.0 score.
    assert import_calls[0].isrc == "REC049"


def test_generate_isrc_not_on_deezer_dropped(stores, paths, with_api_key) -> None:
    """A Last.fm hit with no Deezer match is silently dropped."""
    tracks, albums, recs = stores
    _seed_loved(tracks, "SEED1", "Song", "Artist")
    similar = [{"name": "Ghost", "artist": "NoOne", "mbid": "", "match": 0.7}]

    with (
        patch("music_manager.services.lastfm.get_similar_tracks", return_value=similar),
        patch("music_manager.pipeline.recommend.search_track", return_value=[]),
        patch(
            "music_manager.pipeline.recommend.import_resolved_track", return_value=None
        ) as mock_import,
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks", return_value=[]
        ),
        patch("music_manager.pipeline.recommend.apple.list_playlists", return_value=[]),
        patch("music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"),
    ):
        result = recommend.generate_recommendations(
            mode="general",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
        )
    mock_import.assert_not_called()
    assert result.imported == 0


def test_generate_uses_playlist_seeds_in_playlist_mode(
    stores, paths, with_api_key
) -> None:
    """mode='playlist:Workout' restricts the profile to the playlist's tracks."""
    tracks, albums, recs = stores
    # Two loved tracks; only AP_IN is in the "Workout" playlist.
    tracks.add(
        "AP_IN",
        {
            "isrc": "IN1", "title": "In", "artist": "InArt",
            "loved": True, "play_count": 10, "deezer_id": 1,
        },
    )
    tracks.add(
        "AP_OUT",
        {
            "isrc": "OUT1", "title": "Out", "artist": "OutArt",
            "loved": True, "play_count": 10, "deezer_id": 2,
        },
    )

    seen_seeds: list[tuple[str, str]] = []

    def capture_similar(artist: str, track: str, limit: int = 50):
        seen_seeds.append((artist, track))
        return []

    def fake_playlist_tracks(name: str):
        return ["AP_IN"] if name == "Workout" else []

    with (
        patch(
            "music_manager.services.lastfm.get_similar_tracks",
            side_effect=capture_similar,
        ),
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks",
            side_effect=fake_playlist_tracks,
        ),
        patch(
            "music_manager.pipeline.recommend.apple.list_playlists", return_value=[]
        ),
        patch(
            "music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"
        ),
    ):
        result = recommend.generate_recommendations(
            mode="playlist:Workout",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
        )
    # No Deezer matches → lastfm_empty error, but the seed extraction
    # restricted to "InArt"/"In" must have happened.
    assert ("InArt", "In") in seen_seeds
    assert ("OutArt", "Out") not in seen_seeds
    assert result.error == "lastfm_empty"


def test_generate_discovery_mode_runs_without_crash(stores, paths, with_api_key) -> None:
    """Discovery mode applies the narrow match band; mid-match candidate kept."""
    tracks, albums, recs = stores
    _seed_loved(tracks, "SEED1", "Song", "Artist")

    # Mid match (0.5) is accepted in discovery mode; 0.9 (too obvious) is
    # rejected by _append_unique.
    similar = [
        {"name": "Mid", "artist": "MidArt", "match": 0.5},
        {"name": "Obvious", "artist": "ObvArt", "match": 0.95},
    ]

    def fake_import(track: Track, *_args, **_kwargs):
        track.apple_id = f"AP_{track.isrc}"
        return None

    with (
        patch("music_manager.services.lastfm.get_similar_tracks", return_value=similar),
        patch(
            "music_manager.pipeline.recommend.search_track",
            return_value=[_deezer_match("MID1", "Mid", "MidArt")],
        ),
        patch(
            "music_manager.pipeline.recommend.fetch_album_with_cover",
            return_value={"id": 100, "title": "AlbumX", "cover_url": ""},
        ),
        patch(
            "music_manager.pipeline.recommend.import_resolved_track",
            side_effect=fake_import,
        ),
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks",
            return_value=[],
        ),
        patch(
            "music_manager.pipeline.recommend.apple.list_playlists", return_value=[]
        ),
        patch(
            "music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"
        ) as add_pl,
    ):
        result = recommend.generate_recommendations(
            mode="discovery",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
        )
    assert result.imported == 1
    add_pl.assert_called_once_with(recommend.RECO_FOLDER_NAME, "discovery", ["AP_MID1"])


def test_generate_logs_imported_and_generation_run_events(
    stores, paths, with_api_key
) -> None:
    tracks, albums, recs = stores
    _seed_loved(tracks, "SEED1", "Song", "Artist")
    similar = [{"name": "Hit", "artist": "Pop", "match": 0.9}]

    def fake_import(track: Track, *_args, **_kwargs):
        track.apple_id = "AP_NEW"
        return None

    signals = SignalsLog(paths.signals_log_path)
    with (
        patch("music_manager.services.lastfm.get_similar_tracks", return_value=similar),
        patch(
            "music_manager.pipeline.recommend.search_track",
            return_value=[_deezer_match("REC1", "Hit", "Pop")],
        ),
        patch(
            "music_manager.pipeline.recommend.fetch_album_with_cover",
            return_value={"id": 100, "title": "AlbumX", "cover_url": ""},
        ),
        patch(
            "music_manager.pipeline.recommend.import_resolved_track",
            side_effect=fake_import,
        ),
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks",
            return_value=[],
        ),
        patch(
            "music_manager.pipeline.recommend.apple.list_playlists", return_value=[]
        ),
        patch(
            "music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"
        ),
    ):
        recommend.generate_recommendations(
            mode="library",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
            signals=signals,
        )

    event_types = [event["type"] for event in signals.iter_events()]
    assert "recommend_imported" in event_types
    assert "generation_run" in event_types


def test_generate_discovery_cold_start_uses_chart_fallback(
    stores, paths, with_api_key
) -> None:
    """Discovery on an empty library falls back to Last.fm chart so it isn't empty."""
    tracks, albums, recs = stores
    # No seed tracks at all → no similar/artist fallback possible.
    chart_items = [
        {"name": "Discovery1", "artist": "ChartArt1", "mbid": "", "match": 0.0},
        {"name": "Discovery2", "artist": "ChartArt2", "mbid": "", "match": 0.0},
    ]

    def fake_import(track: Track, *_args, **_kwargs):
        track.apple_id = f"AP_{track.isrc}"
        return None

    with (
        patch(
            "music_manager.services.lastfm.get_chart_top_tracks",
            return_value=chart_items,
        ),
        patch(
            "music_manager.pipeline.recommend.search_track",
            return_value=[_deezer_match("D1", "Discovery1", "ChartArt1")],
        ),
        patch(
            "music_manager.pipeline.recommend.fetch_album_with_cover",
            return_value={"id": 100, "title": "AlbumX", "cover_url": ""},
        ),
        patch(
            "music_manager.pipeline.recommend.import_resolved_track",
            side_effect=fake_import,
        ),
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks",
            return_value=[],
        ),
        patch(
            "music_manager.pipeline.recommend.apple.list_playlists", return_value=[]
        ),
        patch(
            "music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"
        ),
    ):
        result = recommend.generate_recommendations(
            mode="discovery",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
        )
    assert result.imported >= 1
    assert result.error == ""


def test_generate_invalid_mode_returns_error(stores, paths, with_api_key) -> None:
    tracks, albums, recs = stores
    result = recommend.generate_recommendations(
        mode="garbage",
        paths=paths,
        tracks_store=tracks,
        albums_store=albums,
        recs_store=recs,
    )
    assert result.error == "invalid_mode"


def test_generate_writes_active_to_store(stores, paths, with_api_key) -> None:
    """A successful import lands in recommendations.json's active section."""
    tracks, albums, recs = stores
    _seed_loved(tracks, "SEED1", "Song", "Artist")

    similar = [{"name": "Hit", "artist": "Pop", "mbid": "", "match": 0.9}]
    deezer = [_deezer_match("REC1", "Hit", "Pop")]

    def fake_import(track: Track, *_args, **_kwargs):
        track.apple_id = "AP_NEW"
        return None

    with (
        patch("music_manager.services.lastfm.get_similar_tracks", return_value=similar),
        patch("music_manager.pipeline.recommend.search_track", return_value=deezer),
        patch(
            "music_manager.pipeline.recommend.fetch_album_with_cover",
            return_value={"id": 100, "title": "AlbumX", "cover_url": ""},
        ),
        patch(
            "music_manager.pipeline.recommend.import_resolved_track", side_effect=fake_import
        ),
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks", return_value=[]
        ),
        patch("music_manager.pipeline.recommend.apple.list_playlists", return_value=[]),
        patch(
            "music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"
        ) as add_playlist,
    ):
        result = recommend.generate_recommendations(
            mode="general",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
        )

    assert result.imported == 1
    assert recs.is_active("REC1")
    add_playlist.assert_called_once_with(
        recommend.RECO_FOLDER_NAME, "library", ["AP_NEW"]
    )

    # The active entry carries the playlist target for the next scan_outcomes.
    assert recs.all_active()["REC1"]["playlist"] == "library"

    # Persisted to disk so the next scan_outcomes can diff against it.
    assert Path(paths.recommendations_path).exists()


def test_generate_filters_low_match_candidates(stores, paths, with_api_key) -> None:
    """Last.fm match < 0.30 candidates are dropped before resolution."""
    tracks, albums, recs = stores
    _seed_loved(tracks, "SEED1", "Song", "Artist")

    similar = [
        {"name": "Bad", "artist": "X", "mbid": "", "match": 0.05, "playcount": 100},
        {"name": "Good", "artist": "Y", "mbid": "", "match": 0.9, "playcount": 100},
    ]

    search_calls: list[tuple[str, str]] = []

    def fake_search(name: str, artist: str) -> list[dict]:
        search_calls.append((name, artist))
        return [_deezer_match("REC", name, artist)]

    with (
        patch("music_manager.services.lastfm.get_similar_tracks", return_value=similar),
        patch("music_manager.pipeline.recommend.search_track", side_effect=fake_search),
        patch(
            "music_manager.pipeline.recommend.fetch_album_with_cover",
            return_value={"id": 100, "title": "AlbumX", "cover_url": ""},
        ),
        patch("music_manager.pipeline.recommend.import_resolved_track", return_value=None),
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks", return_value=[]
        ),
        patch("music_manager.pipeline.recommend.apple.list_playlists", return_value=[]),
        patch("music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"),
    ):
        recommend.generate_recommendations(
            mode="general",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
        )
    # Only the high-match candidate reaches Deezer resolution.
    assert search_calls == [("Good", "Y")]


def test_generate_diversifies_by_artist(stores, paths, with_api_key) -> None:
    """Top-N selection caps tracks-per-artist to avoid same-artist floods."""
    tracks, albums, recs = stores
    _seed_loved(tracks, "SEED1", "Song", "Artist")

    # 10 candidates all from "PopStar" with descending scores.
    similar = [
        {
            "name": f"T{i}", "artist": "PopStar", "mbid": "",
            "match": 0.9 - i * 0.01, "playcount": 1000,
        }
        for i in range(10)
    ]
    # Plus a few candidates from other artists with lower scores.
    similar += [
        {"name": f"OT{i}", "artist": f"Artist{i}", "mbid": "", "match": 0.4, "playcount": 100}
        for i in range(3)
    ]
    deezer_map = {
        ("T" + str(i), "PopStar"): [_deezer_match(f"P{i:03d}", f"T{i}", "PopStar")]
        for i in range(10)
    }
    deezer_map.update(
        {
            ("OT" + str(i), f"Artist{i}"): [_deezer_match(f"O{i:03d}", f"OT{i}", f"Artist{i}")]
            for i in range(3)
        }
    )

    def fake_search(name: str, artist: str) -> list[dict]:
        return deezer_map.get((name, artist), [])

    import_calls: list[Track] = []

    def fake_import(track: Track, *_args, **_kwargs):
        track.apple_id = f"AP_{track.isrc}"
        import_calls.append(track)
        return None

    with (
        patch("music_manager.services.lastfm.get_similar_tracks", return_value=similar),
        patch("music_manager.pipeline.recommend.search_track", side_effect=fake_search),
        patch(
            "music_manager.pipeline.recommend.fetch_album_with_cover",
            return_value={"id": 100, "title": "AlbumX", "cover_url": ""},
        ),
        patch("music_manager.pipeline.recommend.import_resolved_track", side_effect=fake_import),
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks", return_value=[]
        ),
        patch("music_manager.pipeline.recommend.apple.list_playlists", return_value=[]),
        patch("music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"),
    ):
        recommend.generate_recommendations(
            mode="general",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
            target_count=5,
        )

    popstar_top = [t for t in import_calls[:5] if t.artist == "PopStar"]
    assert len(popstar_top) <= recommend._MAX_TRACKS_PER_ARTIST


def test_generate_skips_seeds_with_high_blacklist_ratio(stores, paths, with_api_key) -> None:
    """Negative reinforcement: a seed with >50% blacklisted history is skipped."""
    tracks, albums, recs = stores
    _seed_loved(tracks, "GOODSEED", "Good", "Artist")
    _seed_loved(tracks, "BADSEED", "Bad", "OtherArtist")

    # Plant blacklist history for BADSEED: 3 blacklisted out of 3.
    for i in range(3):
        recs.add_active(
            {
                "isrc": f"OLD{i}",
                "apple_id": f"OAP{i}",
                "title": "Old",
                "artist": "x",
                "seed_isrc": "BADSEED",
            }
        )
        recs.blacklist(f"OLD{i}")

    similar_calls: list[tuple[str, str]] = []

    def fake_similar(artist: str, track: str, *_args, **_kwargs):
        similar_calls.append((artist, track))
        return []

    with (
        patch("music_manager.services.lastfm.get_similar_tracks", side_effect=fake_similar),
        patch("music_manager.pipeline.recommend.search_track", return_value=[]),
        patch("music_manager.pipeline.recommend.apple.get_playlist_tracks", return_value=[]),
        patch("music_manager.pipeline.recommend.apple.list_playlists", return_value=[]),
        patch("music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"),
    ):
        recommend.generate_recommendations(
            mode="general",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
        )

    queried_seeds = {title for _artist, title in similar_calls}
    assert "Good" in queried_seeds
    assert "Bad" not in queried_seeds  # skipped by reinforcement


def test_generate_mood_uses_tag_top_tracks(stores, paths, with_api_key) -> None:
    """mode='mood:chill' triggers tag.getTopTracks instead of seed-based similar."""
    tracks, albums, recs = stores
    _seed_loved(tracks, "SEED1", "Song", "Artist")

    tag_results = [{"name": "Calm", "artist": "Ambient", "mbid": "", "match": 0}]
    with (
        patch(
            "music_manager.services.lastfm.get_top_tracks_by_tag", return_value=tag_results
        ) as tag_mock,
        patch(
            "music_manager.services.lastfm.get_similar_tracks", return_value=[]
        ) as similar_mock,
        patch("music_manager.pipeline.recommend.search_track", return_value=[]),
        patch(
            "music_manager.pipeline.recommend.apple.get_playlist_tracks", return_value=[]
        ),
        patch("music_manager.pipeline.recommend.apple.list_playlists", return_value=[]),
        patch("music_manager.pipeline.recommend.apple.add_to_playlist_in_folder"),
    ):
        recommend.generate_recommendations(
            mode="mood:chill",
            paths=paths,
            tracks_store=tracks,
            albums_store=albums,
            recs_store=recs,
        )
    tag_mock.assert_called_once()
    similar_mock.assert_not_called()
