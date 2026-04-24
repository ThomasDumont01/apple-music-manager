"""Exhaustive modify_track tests — safety, edge cases, data integrity."""

from pathlib import Path
from unittest.mock import patch

from music_manager.core.config import Paths
from music_manager.options.modify_track import (
    change_cover_track,
    edit_metadata_track,
    search_library,
)
from music_manager.services.tracks import Tracks

# ── search_library edge cases ─────────────────────────────────────────────


def test_search_query_matches_both_title_and_album(tmp_path: Path) -> None:
    """Same track appears in both track and album results."""
    store = Tracks(str(tmp_path / "t.json"))
    store.add("A1", {"title": "Rain", "artist": "Art", "album": "Rain", "deezer_id": 1})

    tracks, albums = search_library("rain", store)
    assert len(tracks) == 1
    assert len(albums) == 1


def test_search_store_with_missing_keys(tmp_path: Path) -> None:
    """Store entries with missing title/artist don't crash."""
    store = Tracks(str(tmp_path / "t.json"))
    store.add("A1", {"album": "Only Album", "deezer_id": 1})  # no title, no artist

    tracks, albums = search_library("only", store)
    # Should not crash, entry skipped
    assert len(tracks) == 0


def test_search_returns_correct_track_match_fields(tmp_path: Path) -> None:
    """TrackMatch has all fields populated."""
    store = Tracks(str(tmp_path / "t.json"))
    store.add(
        "AP1",
        {
            "title": "Song",
            "artist": "Art",
            "album": "Al",
            "isrc": "ISRC1",
            "deezer_id": 42,
        },
    )

    tracks, _ = search_library("song", store)
    assert len(tracks) == 1
    t = tracks[0]
    assert t.apple_id == "AP1"
    assert t.title == "Song"
    assert t.artist == "Art"
    assert t.album == "Al"
    assert t.isrc == "ISRC1"
    assert t.deezer_id == 42


def test_search_album_groups_tracks(tmp_path: Path) -> None:
    """AlbumMatch contains all tracks from the album."""
    store = Tracks(str(tmp_path / "t.json"))
    store.add("A1", {"title": "T1", "artist": "Art", "album": "Thriller", "deezer_id": 1})
    store.add("A2", {"title": "T2", "artist": "Art", "album": "Thriller", "deezer_id": 1})
    store.add("A3", {"title": "T3", "artist": "Art2", "album": "Thriller", "deezer_id": 1})

    _, albums = search_library("thriller", store)
    assert len(albums) == 1
    assert albums[0].track_count == 3
    assert len(albums[0].tracks) == 3


# ── edit_metadata_track — index update ────────────────────────────────────


def test_edit_metadata_title_updates_index(tmp_path: Path) -> None:
    """After editing title, the new title is findable via index."""
    store = Tracks(str(tmp_path / "t.json"))
    store.add("A1", {"title": "Old Title", "artist": "Artist", "isrc": ""})

    with patch("music_manager.services.apple.update_track"):
        edit_metadata_track("A1", {"title": "New Title"}, store)

    # New title should be indexed
    assert store.get_by_title_artist("new title", "artist") is not None
    # Dirty flag set (via tracks_store.update)
    assert store._dirty


def test_edit_metadata_preserves_other_fields(tmp_path: Path) -> None:
    """Editing one field doesn't affect other fields."""
    store = Tracks(str(tmp_path / "t.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Art",
            "album": "Al",
            "genre": "Pop",
            "deezer_id": 1,
            "isrc": "X",
        },
    )

    with patch("music_manager.services.apple.update_track"):
        edit_metadata_track("A1", {"genre": "Rock"}, store)

    entry = store.get_by_apple_id("A1")
    assert entry is not None
    assert entry["genre"] == "Rock"
    assert entry["title"] == "Song"  # unchanged
    assert entry["deezer_id"] == 1  # unchanged
    assert entry["isrc"] == "X"  # unchanged


# ── change_cover_track ────────────────────────────────────────────────────


@patch("music_manager.services.apple.set_artwork")
@patch("music_manager.services.resolver.download_cover_file", return_value="")
def test_cover_download_fails(mock_dl, mock_art, tmp_path) -> None:
    """Cover download failure → error, set_artwork NOT called."""
    store = Tracks(str(tmp_path / "t.json"))
    store.add("A1", {"title": "Song"})
    paths = Paths(str(tmp_path / "data"))

    result = change_cover_track("A1", "https://bad.url", store, paths)
    assert result.success is False
    assert result.error == "cover_download_failed"
    mock_art.assert_not_called()


# ── Data integrity: add then remove same apple_id ─────────────────────────


def test_add_then_remove_leaves_clean_state(tmp_path: Path) -> None:
    """add() then remove() leaves all indexes clean."""
    store = Tracks(str(tmp_path / "t.json"))
    store.add(
        "A1",
        {
            "title": "Song",
            "artist": "Art",
            "isrc": "ISRC1",
            "csv_title": "CSV Song",
            "csv_artist": "Art",
        },
    )
    store.remove("A1")

    assert store.get_by_apple_id("A1") is None
    assert store.get_by_isrc("ISRC1") is None
    assert store.get_by_title_artist("song", "art") is None
    assert store.get_by_title_artist("csv song", "art") is None
    assert len(store.all()) == 0


# ── Data integrity: save → reload → verify indexes ───────────────────────


def test_save_reload_all_indexes_consistent(tmp_path: Path) -> None:
    """Save and reload preserves all index lookups."""
    path = str(tmp_path / "tracks.json")
    store = Tracks(path)

    for i in range(20):
        store.add(
            f"A{i}",
            {
                "title": f"Song {i}",
                "artist": f"Artist {i % 5}",
                "isrc": f"ISRC{i:04d}",
                "album": f"Album {i % 3}",
            },
        )

    store.save()
    store2 = Tracks(path)

    assert len(store2.all()) == 20
    for i in range(20):
        assert store2.get_by_isrc(f"ISRC{i:04d}") is not None
        assert store2.get_by_isrc(f"isrc{i:04d}") is not None  # case insensitive
        assert store2.get_by_apple_id(f"A{i}") is not None


# ── Title+artist index collision ──────────────────────────────────────────


def test_title_artist_index_last_wins(tmp_path: Path) -> None:
    """Two tracks same title+artist: index points to last added."""
    store = Tracks(str(tmp_path / "t.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "isrc": "I1"})
    store.add("A2", {"title": "Song", "artist": "Art", "isrc": "I2"})

    entry = store.get_by_title_artist("song", "art")
    assert entry is not None
    # Last added wins
    assert entry.get("isrc") == "I2"
    # But both still exist
    assert len(store.all()) == 2


def test_csv_title_index_first_wins(tmp_path: Path) -> None:
    """csv_title index uses setdefault → first wins."""
    store = Tracks(str(tmp_path / "t.json"))
    store.add(
        "A1",
        {
            "title": "T1",
            "artist": "Art",
            "csv_title": "CSV",
            "csv_artist": "Art",
            "isrc": "I1",
        },
    )
    store.add(
        "A2",
        {
            "title": "T2",
            "artist": "Art2",
            "csv_title": "CSV",
            "csv_artist": "Art",
            "isrc": "I2",
        },
    )

    entry = store.get_by_title_artist("csv", "art")
    assert entry is not None
    assert entry.get("isrc") == "I1"  # first wins (setdefault)
