"""Tests for options/find_duplicates.py."""

from pathlib import Path
from unittest.mock import patch

from music_manager.options.find_duplicates import (
    best_version,
    find_duplicates,
    group_key,
    ignore_group,
    load_ignored,
    remove_duplicates,
)
from music_manager.services.tracks import Tracks

# ── Filtering ──────────────────────────────────────────────────────────────


def test_excludes_unidentified(tmp_path: Path) -> None:
    """Tracks without deezer_id are excluded."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art"})
    store.add("A2", {"title": "Song", "artist": "Art"})
    assert find_duplicates(store) == []


def test_excludes_failed(tmp_path: Path) -> None:
    """Failed tracks excluded even with matching deezer_id."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 123, "status": "failed"})
    store.add("A2", {"title": "Song", "artist": "Art", "deezer_id": 123, "status": "failed"})
    assert find_duplicates(store) == []


def test_mixed_identified_unidentified(tmp_path: Path) -> None:
    """Unidentified tracks ignored — only identified checked."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 123})
    store.add("A2", {"title": "Song", "artist": "Art"})  # no deezer_id
    assert find_duplicates(store) == []


# ── Grouping by deezer_id ─────────────────────────────────────────────────


def test_same_deezer_id(tmp_path: Path) -> None:
    """Two entries same deezer_id → duplicate group."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 123})
    store.add("A2", {"title": "Song", "artist": "Art", "deezer_id": 123})
    groups = find_duplicates(store)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_different_deezer_id_different_song(tmp_path: Path) -> None:
    """Different songs → no duplicates."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song A", "artist": "Art1", "deezer_id": 100})
    store.add("A2", {"title": "Song B", "artist": "Art2", "deezer_id": 200})
    assert find_duplicates(store) == []


def test_multiple_groups(tmp_path: Path) -> None:
    """Multiple independent duplicate groups."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song1", "artist": "Art", "deezer_id": 100})
    store.add("A2", {"title": "Song1", "artist": "Art", "deezer_id": 100})
    store.add("A3", {"title": "Song2", "artist": "Art", "deezer_id": 200})
    store.add("A4", {"title": "Song2", "artist": "Art", "deezer_id": 200})
    groups = find_duplicates(store)
    assert len(groups) == 2


def test_three_duplicates(tmp_path: Path) -> None:
    """Three entries same deezer_id → group of 3."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 123})
    store.add("A2", {"title": "Song", "artist": "Art", "deezer_id": 123})
    store.add("A3", {"title": "Song", "artist": "Art", "deezer_id": 123})
    groups = find_duplicates(store)
    assert len(groups) == 1
    assert len(groups[0]) == 3


# ── ISRC merge ─────────────────────────────────────────────────────────────


def test_merge_by_isrc(tmp_path: Path) -> None:
    """Different deezer_id, same ISRC → merged into one group."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 100, "isrc": "USRC1"})
    store.add("A2", {"title": "Song", "artist": "Art", "deezer_id": 200, "isrc": "USRC1"})
    groups = find_duplicates(store)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_isrc_case_insensitive(tmp_path: Path) -> None:
    """ISRC comparison case-insensitive."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 100, "isrc": "usrc1"})
    store.add("A2", {"title": "Song", "artist": "Art", "deezer_id": 200, "isrc": "USRC1"})
    groups = find_duplicates(store)
    assert len(groups) == 1


# ── Title+artist merge ─────────────────────────────────────────────────────


def test_merge_by_title_artist(tmp_path: Path) -> None:
    """Same title+artist, different deezer_id, no ISRC → merged."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Bohemian Rhapsody", "artist": "Queen", "deezer_id": 100})
    store.add("A2", {"title": "Bohemian Rhapsody", "artist": "Queen", "deezer_id": 200})
    groups = find_duplicates(store)
    assert len(groups) == 1


def test_no_merge_different_artist(tmp_path: Path) -> None:
    """Same title, different artist → NOT merged."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Artist A", "deezer_id": 100})
    store.add("A2", {"title": "Song", "artist": "Artist B", "deezer_id": 200})
    assert find_duplicates(store) == []


def test_no_merge_conflicting_isrc(tmp_path: Path) -> None:
    """Same title+artist, different ISRCs → NOT merged (different recordings)."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 100, "isrc": "AAA"})
    store.add("A2", {"title": "Song", "artist": "Art", "deezer_id": 200, "isrc": "BBB"})
    assert find_duplicates(store) == []


def test_merge_one_has_isrc(tmp_path: Path) -> None:
    """Same title+artist, one has ISRC, other doesn't → merged (no conflict)."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 100, "isrc": "AAA"})
    store.add("A2", {"title": "Song", "artist": "Art", "deezer_id": 200})
    groups = find_duplicates(store)
    assert len(groups) == 1


def test_merge_first_artist_only(tmp_path: Path) -> None:
    """Multi-artist: uses first_artist for matching."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art, Feat B", "deezer_id": 100})
    store.add("A2", {"title": "Song", "artist": "Art", "deezer_id": 200})
    groups = find_duplicates(store)
    assert len(groups) == 1


def test_merge_title_artist_after_isrc_merge(tmp_path: Path) -> None:
    """After ISRC merge, title+artist still catches remaining groups.

    Group A: "Song" (dz:100, ISRC:X1) + "Song Remix" (dz:200, ISRC:X1) — merged by ISRC.
    Group B: "Song" (dz:300, no ISRC) — should merge with A via title+artist.
    """
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 100, "isrc": "X1"})
    store.add("A2", {"title": "Song Remix", "artist": "Art", "deezer_id": 200, "isrc": "X1"})
    store.add("A3", {"title": "Song", "artist": "Art", "deezer_id": 300})
    groups = find_duplicates(store)
    assert len(groups) == 1
    assert len(groups[0]) == 3


# ── Edge cases ─────────────────────────────────────────────────────────────


def test_empty_store(tmp_path: Path) -> None:
    """Empty store → no duplicates."""
    store = Tracks(str(tmp_path / "tracks.json"))
    assert find_duplicates(store) == []


def test_apple_id_preserved(tmp_path: Path) -> None:
    """Each entry has _apple_id field."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 123})
    store.add("A2", {"title": "Song", "artist": "Art", "deezer_id": 123})
    groups = find_duplicates(store)
    ids = {e["_apple_id"] for e in groups[0]}
    assert ids == {"A1", "A2"}


# ── best_version ───────────────────────────────────────────────────────────


def test_best_version_prefers_deezer_id() -> None:
    """Entry with deezer_id preferred."""
    group = [
        {"deezer_id": 0, "isrc": "", "duration": 300},
        {"deezer_id": 123, "isrc": "X1", "duration": 250},
    ]
    assert best_version(group) == 1


def test_best_version_prefers_isrc() -> None:
    """Between equal entries, one with ISRC preferred."""
    group = [
        {"deezer_id": 123, "isrc": "", "duration": 200},
        {"deezer_id": 123, "isrc": "X1", "duration": 200},
    ]
    assert best_version(group) == 1


def test_best_version_prefers_duration() -> None:
    """Longer duration preferred."""
    group = [
        {"deezer_id": 123, "isrc": "X1", "duration": 180},
        {"deezer_id": 123, "isrc": "X1", "duration": 250},
    ]
    assert best_version(group) == 1


def test_best_version_single() -> None:
    """Single entry → index 0."""
    group = [{"deezer_id": 123, "isrc": "X1", "duration": 200}]
    assert best_version(group) == 0


def test_best_version_duration_none() -> None:
    """duration=None does not crash — treated as 0."""
    group = [
        {"deezer_id": 123, "isrc": "X1", "duration": None},
        {"deezer_id": 123, "isrc": "X1", "duration": 200},
    ]
    assert best_version(group) == 1


# ── remove_duplicates ─────────────────────────────────────────────────────


@patch("music_manager.options.find_duplicates.delete_tracks", return_value=2)
def test_remove_duplicates_deletes_from_store_and_apple(mock_delete, tmp_path: Path) -> None:
    """Removes tracks from store and calls delete_tracks."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 123})
    store.add("A2", {"title": "Song", "artist": "Art", "deezer_id": 123})
    store.add("A3", {"title": "Other", "artist": "Art", "deezer_id": 456})

    count = remove_duplicates(["A1", "A2"], store)

    assert count == 2
    mock_delete.assert_called_once_with(["A1", "A2"])
    assert store.get_by_apple_id("A1") is None
    assert store.get_by_apple_id("A2") is None
    assert store.get_by_apple_id("A3") is not None


@patch("music_manager.options.find_duplicates.delete_tracks", return_value=0)
def test_remove_duplicates_nonexistent_no_crash(mock_delete, tmp_path: Path) -> None:
    """Removing nonexistent apple_ids does not crash."""
    store = Tracks(str(tmp_path / "tracks.json"))
    count = remove_duplicates(["NOPE"], store)
    assert count == 0


@patch("music_manager.options.find_duplicates.delete_tracks", return_value=1)
def test_remove_duplicates_cleans_indexes(mock_delete, tmp_path: Path) -> None:
    """After removal, ISRC and title+artist indexes are cleaned."""
    store = Tracks(str(tmp_path / "tracks.json"))
    store.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 123, "isrc": "X1"})
    store.add("A2", {"title": "Song", "artist": "Art", "deezer_id": 123, "isrc": "X1"})

    remove_duplicates(["A1"], store)

    assert store.get_by_isrc("X1") is not None  # A2 still indexed
    assert store.get_by_apple_id("A1") is None


# ── group_key ──────────────────────────────────────────────────────────────


def test_group_key_same_deezer_id() -> None:
    """Group with same deezer_id → single id as key."""
    group = [
        {"deezer_id": 100, "_apple_id": "A1"},
        {"deezer_id": 100, "_apple_id": "A2"},
    ]
    assert group_key(group) == "100"


def test_group_key_merged_ids_sorted() -> None:
    """Merged group → sorted deezer_ids."""
    group = [
        {"deezer_id": 300, "_apple_id": "A1"},
        {"deezer_id": 100, "_apple_id": "A2"},
    ]
    assert group_key(group) == "100,300"


def test_group_key_deduplicates() -> None:
    """Repeated deezer_ids are deduplicated."""
    group = [
        {"deezer_id": 100, "_apple_id": "A1"},
        {"deezer_id": 100, "_apple_id": "A2"},
        {"deezer_id": 200, "_apple_id": "A3"},
    ]
    assert group_key(group) == "100,200"


# ── ignore_group / load_ignored ────────────────────────────────────────────


def test_ignore_and_load(tmp_path: Path) -> None:
    """ignore_group persists, load_ignored reads back."""
    prefs_path = str(tmp_path / "prefs.json")
    group = [{"deezer_id": 100}, {"deezer_id": 100}]

    ignore_group(group, prefs_path)

    ignored = load_ignored(prefs_path)
    assert "100" in ignored


def test_ignore_multiple_groups(tmp_path: Path) -> None:
    """Multiple ignores accumulate."""
    prefs_path = str(tmp_path / "prefs.json")

    ignore_group([{"deezer_id": 100}, {"deezer_id": 100}], prefs_path)
    ignore_group([{"deezer_id": 200}, {"deezer_id": 300}], prefs_path)

    ignored = load_ignored(prefs_path)
    assert ignored == {"100", "200,300"}


def test_load_ignored_empty(tmp_path: Path) -> None:
    """No prefs file → empty set."""
    ignored = load_ignored(str(tmp_path / "nonexistent.json"))
    assert ignored == set()
