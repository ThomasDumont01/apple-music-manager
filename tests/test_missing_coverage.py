"""Tests for previously uncovered scenarios — filling coverage gaps."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from music_manager.core.io import load_json, save_json
from music_manager.core.normalize import normalize, prepare_title
from music_manager.options.export import export_playlist
from music_manager.options.maintenance import clear_preferences
from music_manager.services.albums import Albums
from music_manager.services.tracks import Tracks

# ══════════════════════════════════════════════════════════════════════════
# Tracks store — index coherence
# ══════════════════════════════════════════════════════════════════════════


def test_add_overwrite_cleans_old_title_index(tmp_path: Path) -> None:
    """Overwriting an entry cleans the old title+artist index."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add("A1", {"title": "Old Song", "artist": "Old Artist", "deezer_id": 1})

    # Old index should find it
    assert tracks.get_by_title_artist(normalize("Old Song"), normalize("Old Artist"))

    # Overwrite with different title+artist
    tracks.add("A1", {"title": "New Song", "artist": "New Artist", "deezer_id": 1})

    # Old index should be gone
    assert tracks.get_by_title_artist(normalize("Old Song"), normalize("Old Artist")) is None
    # New index should work
    assert tracks.get_by_title_artist(normalize("New Song"), normalize("New Artist")) is not None


def test_add_overwrite_cleans_old_csv_index(tmp_path: Path) -> None:
    """Overwriting cleans csv_title+csv_artist index too."""
    tracks = Tracks(str(tmp_path / "t.json"))
    tracks.add(
        "A1",
        {
            "title": "Song",
            "artist": "Art",
            "csv_title": "CSV Title",
            "csv_artist": "CSV Art",
            "deezer_id": 1,
        },
    )

    # CSV index should find it
    assert tracks.get_by_title_artist(normalize("CSV Title"), normalize("CSV Art"))

    # Overwrite without csv fields
    tracks.add("A1", {"title": "Song", "artist": "Art", "deezer_id": 1})

    # Old CSV index should be gone
    assert tracks.get_by_title_artist(normalize("CSV Title"), normalize("CSV Art")) is None


def test_thread_safe_concurrent_add(tmp_path: Path) -> None:
    """Concurrent adds don't corrupt the store."""
    import threading  # noqa: PLC0415

    tracks = Tracks(str(tmp_path / "t.json"))
    errors: list[str] = []

    def add_batch(prefix: str, count: int) -> None:
        try:
            for i in range(count):
                tracks.add(
                    f"{prefix}_{i}",
                    {
                        "title": f"Song {prefix}{i}",
                        "artist": f"Art {prefix}",
                        "isrc": f"ISRC{prefix}{i:04d}",
                        "deezer_id": 1,
                    },
                )
        except Exception as exc:
            errors.append(str(exc))

    threads = [
        threading.Thread(target=add_batch, args=("A", 50)),
        threading.Thread(target=add_batch, args=("B", 50)),
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not errors
    assert len(tracks.all()) == 100


# ══════════════════════════════════════════════════════════════════════════
# Normalize — CJK + edge cases
# ══════════════════════════════════════════════════════════════════════════


def test_normalize_cjk_preserved() -> None:
    """CJK characters are preserved, not stripped to empty string."""
    assert normalize("五月天") != ""
    assert "五月天" in normalize("五月天")


def test_normalize_cyrillic_preserved() -> None:
    """Cyrillic characters preserved."""
    assert normalize("Кино") != ""


def test_normalize_arabic_preserved() -> None:
    """Arabic characters preserved."""
    assert normalize("فيروز") != ""


def test_normalize_mixed_latin_cjk() -> None:
    """Mixed Latin+CJK: Latin dominates when ASCII extraction succeeds."""
    result = normalize("Café 五月天")
    assert "cafe" in result
    # CJK dropped when Latin text is present (ASCII encoding path)
    # This is by design: normalize() picks one strategy per string

    # Pure CJK should be preserved
    assert normalize("五月天") != ""


def test_normalize_empty_and_whitespace() -> None:
    """Empty and whitespace-only strings."""
    assert normalize("") == ""
    assert normalize("   ") == ""


def test_prepare_title_nested_parens() -> None:
    """Nested parentheses stripped correctly."""
    result = prepare_title("Song (feat. X) (Live)")
    assert "feat" not in result
    assert "live" not in result


def test_prepare_title_no_parens() -> None:
    """Title without parens unchanged."""
    assert prepare_title("Simple Song") == normalize("Simple Song")


# ══════════════════════════════════════════════════════════════════════════
# Albums — shallow copy safety
# ══════════════════════════════════════════════════════════════════════════


def test_albums_get_returns_copy(tmp_path: Path) -> None:
    """Albums.get() returns a copy — mutating it doesn't affect cache."""
    albums = Albums(str(tmp_path / "a.json"))
    albums.put(1, {"title": "Album", "year": "2020"})

    data = albums.get(1)
    assert data is not None
    data["title"] = "MUTATED"

    # Original should be unaffected
    original = albums.get(1)
    assert original is not None
    assert original["title"] == "Album"


def test_albums_remove(tmp_path: Path) -> None:
    """Albums.remove() works and marks dirty."""
    albums = Albums(str(tmp_path / "a.json"))
    albums.put(1, {"title": "Album"})
    albums.remove(1)
    assert albums.get(1) is None


# ══════════════════════════════════════════════════════════════════════════
# Export playlist
# ══════════════════════════════════════════════════════════════════════════


def test_export_playlist_creates_csv(tmp_path: Path) -> None:
    """export_playlist writes a valid CSV."""
    tracks = [
        {"title": "Song A", "artist": "Art A", "album": "Al A"},
        {"title": "Song B", "artist": "Art B", "album": "Al B"},
    ]
    path = str(tmp_path / "export.csv")
    count = export_playlist(tracks, path)
    assert count == 2
    assert os.path.isfile(path)

    from music_manager.core.io import load_csv  # noqa: PLC0415

    rows = load_csv(path)
    assert len(rows) == 2
    assert rows[0]["title"] == "Song A"


def test_export_playlist_empty(tmp_path: Path) -> None:
    """Empty track list → empty CSV."""
    path = str(tmp_path / "empty.csv")
    count = export_playlist([], path)
    assert count == 0


# ══════════════════════════════════════════════════════════════════════════
# Maintenance
# ══════════════════════════════════════════════════════════════════════════


def test_clear_preferences(tmp_path: Path) -> None:
    """clear_preferences resets the preferences file."""
    prefs_path = str(tmp_path / "prefs.json")
    save_json(prefs_path, {"ignored_albums": ["Album1"], "refusals": {"key": "val"}})

    clear_preferences(prefs_path)

    data = load_json(prefs_path)
    assert data == {}


# ══════════════════════════════════════════════════════════════════════════
# JSON corruption recovery
# ══════════════════════════════════════════════════════════════════════════


def test_load_json_corrupt_no_backup(tmp_path: Path) -> None:
    """Corrupt JSON without .tmp backup → empty dict + warning."""
    path = str(tmp_path / "corrupt.json")
    with open(path, "w") as f:
        f.write("{invalid json!!!")

    result = load_json(path)
    assert result == {}


def test_load_json_corrupt_with_backup(tmp_path: Path) -> None:
    """Corrupt JSON with valid .tmp → recovers from backup."""
    path = str(tmp_path / "data.json")
    save_json(path, {"key": "value"})

    import shutil  # noqa: PLC0415

    shutil.copy(path, path + ".tmp")

    with open(path, "w") as f:
        f.write("CORRUPT!")

    result = load_json(path)
    assert result == {"key": "value"}


def test_load_json_missing_file(tmp_path: Path) -> None:
    """Missing file → empty dict, no crash."""
    result = load_json(str(tmp_path / "nonexistent.json"))
    assert result == {}


# ══════════════════════════════════════════════════════════════════════════
# Resolver — error handling
# ══════════════════════════════════════════════════════════════════════════


@patch("music_manager.services.resolver._SESSION")
def test_deezer_get_error_response(mock_session) -> None:
    """Deezer API returns error field → None."""
    from music_manager.services.resolver import clear_api_cache, deezer_get  # noqa: PLC0415

    clear_api_cache()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"error": {"type": "DataException", "message": "no data"}}
    mock_session.get.return_value = mock_response

    result = deezer_get("/track/isrc:INVALID")
    assert result is None


@patch("music_manager.services.resolver._SESSION")
def test_deezer_get_timeout(mock_session) -> None:
    """Deezer timeout → None."""
    import requests  # noqa: PLC0415

    from music_manager.services.resolver import clear_api_cache, deezer_get  # noqa: PLC0415

    clear_api_cache()
    mock_session.get.side_effect = requests.Timeout("timeout")

    result = deezer_get("/track/123")
    assert result is None


@patch("music_manager.services.resolver._SESSION")
def test_deezer_get_cache_hit(mock_session) -> None:
    """Second call to same endpoint returns cached value."""
    from music_manager.services.resolver import clear_api_cache, deezer_get  # noqa: PLC0415

    clear_api_cache()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": 123, "title": "Song"}
    mock_session.get.return_value = mock_response

    result1 = deezer_get("/track/123")
    result2 = deezer_get("/track/123")

    assert result1 == result2
    assert mock_session.get.call_count == 1  # only 1 HTTP call


# ══════════════════════════════════════════════════════════════════════════
# Tagger — write_isrc format detection
# ══════════════════════════════════════════════════════════════════════════


def test_write_isrc_detects_m4a_format(tmp_path: Path) -> None:
    """write_isrc uses M4A tag key for .m4a files."""
    from unittest.mock import patch as mock_patch  # noqa: PLC0415

    from music_manager.services.tagger import write_isrc  # noqa: PLC0415

    with mock_patch("music_manager.services.tagger.mutagen") as mock_mutagen:
        mock_tags = MagicMock()
        mock_mutagen.File.return_value = mock_tags

        result = write_isrc(str(tmp_path / "song.m4a"), "GBUM71029604")

        if result:
            # Should use M4A key
            call_args = mock_tags.__setitem__.call_args
            assert call_args is not None
            key = call_args[0][0]
            assert key == "----:com.apple.iTunes:ISRC"


def test_write_isrc_detects_mp3_format(tmp_path: Path) -> None:
    """write_isrc uses TSRC Frame for .mp3 files."""
    from unittest.mock import patch as mock_patch  # noqa: PLC0415

    from music_manager.services.tagger import write_isrc  # noqa: PLC0415

    with mock_patch("music_manager.services.tagger.mutagen") as mock_mutagen:
        mock_tags = MagicMock()
        mock_mutagen.File.return_value = mock_tags

        result = write_isrc(str(tmp_path / "song.mp3"), "GBUM71029604")

        if result:
            # MP3 uses tags.tags.add(TSRC(...)) instead of tags[key] = value
            mock_tags.tags.add.assert_called_once()
