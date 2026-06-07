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


# ── scan_deleted ────────────────────────────────────────────────────────────


def test_scan_deleted_blacklists_missing(stores) -> None:
    """ISRCs present in active but missing from the playlist get blacklisted."""
    _tracks, _albums, recs = stores
    recs.add_active({"isrc": "X1", "apple_id": "AP1", "title": "T", "artist": "A"})
    recs.add_active({"isrc": "X2", "apple_id": "AP2", "title": "T2", "artist": "B"})

    with (
        patch("music_manager.pipeline.recommend.apple.get_playlist_tracks", return_value=["AP2"]),
        patch("music_manager.pipeline.recommend.apple.list_playlists", return_value=[]),
    ):
        moved = recommend.scan_deleted(recs)
    assert moved == 1
    assert recs.is_blacklisted("X1")
    assert recs.is_active("X2")


def test_scan_deleted_playlist_absent_no_op(stores) -> None:
    """If the playlist isn't present, don't mass-blacklist."""
    _tracks, _albums, recs = stores
    recs.add_active({"isrc": "X1", "apple_id": "AP1", "title": "T", "artist": "A"})
    with (
        patch("music_manager.pipeline.recommend.apple.get_playlist_tracks", return_value=[]),
        patch("music_manager.pipeline.recommend.apple.list_playlists", return_value=[]),
    ):
        moved = recommend.scan_deleted(recs)
    assert moved == 0
    assert recs.is_active("X1")


def test_scan_deleted_empty_playlist_but_exists_blacklists_all(stores) -> None:
    """If the playlist exists but is empty → user wiped it → blacklist everything."""
    _tracks, _albums, recs = stores
    recs.add_active({"isrc": "X1", "apple_id": "AP1", "title": "T", "artist": "A"})
    with (
        patch("music_manager.pipeline.recommend.apple.get_playlist_tracks", return_value=[]),
        patch(
            "music_manager.pipeline.recommend.apple.list_playlists",
            return_value=[(recommend.PLAYLIST_NAME, 0)],
        ),
    ):
        moved = recommend.scan_deleted(recs)
    assert moved == 1
    assert recs.is_blacklisted("X1")


def test_scan_deleted_no_active_no_op(stores) -> None:
    """Empty active section → no work."""
    _tracks, _albums, recs = stores
    assert recommend.scan_deleted(recs) == 0


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
        patch("music_manager.pipeline.recommend.apple.add_to_playlist"),
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
        patch("music_manager.pipeline.recommend.apple.add_to_playlist"),
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
        patch("music_manager.pipeline.recommend.apple.add_to_playlist"),
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
        patch("music_manager.pipeline.recommend.apple.add_to_playlist"),
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
        patch("music_manager.pipeline.recommend.apple.add_to_playlist"),
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
        patch("music_manager.pipeline.recommend.apple.add_to_playlist") as add_playlist,
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
    add_playlist.assert_called_once_with(recommend.PLAYLIST_NAME, ["AP_NEW"])

    # Persisted to disk so the next scan_deleted can diff against it.
    assert (Path(paths.recommendations_path)).exists()


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
        patch("music_manager.pipeline.recommend.apple.add_to_playlist"),
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
        patch("music_manager.pipeline.recommend.apple.add_to_playlist"),
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
        patch("music_manager.pipeline.recommend.apple.add_to_playlist"),
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
        patch("music_manager.pipeline.recommend.apple.add_to_playlist"),
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
