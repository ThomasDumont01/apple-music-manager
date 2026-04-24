"""Unit tests for services/apple.py — AppleScript logic (portable, no macOS required)."""

from unittest.mock import patch

import pytest

from music_manager.services.apple import (
    _esc,
    add_to_playlist,
    delete_tracks,
    get_playlist_tracks,
    import_file,
    list_playlists,
    rebuild_playlist,
    set_artwork,
    set_artwork_batch,
    update_track,
    update_tracks_batch,
)

_PATCH = "music_manager.services.apple.run_applescript"


# ── _esc ────────────────────────────────────────────────────────────────────


def test_esc_double_quotes() -> None:
    """Double quotes are escaped for AppleScript."""
    assert _esc('Say "hello"') == 'Say \\"hello\\"'


def test_esc_backslash() -> None:
    """Backslashes are escaped for AppleScript."""
    assert _esc("path\\to\\file") == "path\\\\to\\\\file"


def test_esc_combined() -> None:
    """Both quotes and backslashes are escaped."""
    assert _esc('a\\b"c') == 'a\\\\b\\"c'


# ── update_track ────────────────────────────────────────────────────────────


@patch(_PATCH)
def test_update_track_string_fields(mock_script) -> None:
    """String fields produce quoted AppleScript set statements."""
    mock_script.return_value = ""
    update_track("ABC123", {"title": "New Title", "artist": "New Artist"})

    mock_script.assert_called_once()
    script = mock_script.call_args[0][0]
    assert 'set name of t to "New Title"' in script
    assert 'set artist of t to "New Artist"' in script
    assert 'persistent ID is "ABC123"' in script


@patch(_PATCH)
def test_update_track_int_fields(mock_script) -> None:
    """Int fields produce unquoted AppleScript set statements."""
    mock_script.return_value = ""
    update_track("ABC123", {"track_number": 5, "year": 1975})

    script = mock_script.call_args[0][0]
    assert "set track number of t to 5" in script
    assert "set year of t to 1975" in script


@patch(_PATCH)
def test_update_track_all_field_mappings(mock_script) -> None:
    """All supported fields map correctly to AppleScript property names."""
    mock_script.return_value = ""
    update_track(
        "X",
        {
            "title": "T",
            "artist": "A",
            "album": "Al",
            "genre": "G",
            "year": 2000,
            "track_number": 1,
            "total_tracks": 10,
            "disk_number": 2,
            "album_artist": "AA",
        },
    )

    script = mock_script.call_args[0][0]
    assert 'set name of t to "T"' in script
    assert 'set album of t to "Al"' in script
    assert 'set genre of t to "G"' in script
    assert "set year of t to 2000" in script
    assert "set track number of t to 1" in script
    assert "set track count of t to 10" in script
    assert "set disc number of t to 2" in script
    assert 'set album artist of t to "AA"' in script


@patch(_PATCH)
def test_update_track_unknown_field_ignored(mock_script) -> None:
    """Unknown fields are silently ignored — no AppleScript call."""
    update_track("ABC123", {"unknown_field": "value"})
    mock_script.assert_not_called()


@patch(_PATCH)
def test_update_track_escapes_values(mock_script) -> None:
    """String values with quotes are properly escaped."""
    mock_script.return_value = ""
    update_track("ABC123", {"title": 'Rock "n" Roll'})

    script = mock_script.call_args[0][0]
    assert 'Rock \\"n\\" Roll' in script


# ── import_file ─────────────────────────────────────────────────────────────


@patch(_PATCH, return_value="NEW_ID_123")
def test_import_file_returns_apple_id(mock_script) -> None:
    """import_file returns the apple_id from AppleScript output."""
    result = import_file("/tmp/song.m4a")

    assert result == "NEW_ID_123"
    script = mock_script.call_args[0][0]
    assert "add POSIX file" in script
    assert "song.m4a" in script
    assert "persistent ID" in script


@patch(_PATCH, return_value=None)
def test_import_file_raises_on_failure(mock_script) -> None:
    """import_file raises RuntimeError when AppleScript fails."""
    with pytest.raises(RuntimeError, match="Import failed"):
        import_file("/tmp/song.m4a")


# ── delete_tracks ───────────────────────────────────────────────────────────


@patch(_PATCH, return_value="3")
def test_delete_tracks_success(mock_script) -> None:
    """delete_tracks batches all IDs in one AppleScript call."""
    count = delete_tracks(["A1", "A2", "A3"])

    assert count == 3
    assert mock_script.call_count == 1
    assert "delete" in mock_script.call_args[0][0]


@patch(_PATCH, return_value=None)
def test_delete_tracks_all_fail(mock_script) -> None:
    """delete_tracks returns 0 when AppleScript fails."""
    count = delete_tracks(["A1", "A2"])
    assert count == 0


@patch(_PATCH, return_value="1")
def test_delete_tracks_partial(mock_script) -> None:
    """delete_tracks returns count from AppleScript."""
    count = delete_tracks(["A1", "A2", "A3"])
    assert count == 1


def test_delete_tracks_empty_list() -> None:
    """delete_tracks with empty list does nothing."""
    count = delete_tracks([])
    assert count == 0


# ── set_artwork ─────────────────────────────────────────────────────────────


@patch(_PATCH)
def test_set_artwork_script(mock_script) -> None:
    """set_artwork generates correct AppleScript referencing image path."""
    set_artwork("ABC123", "/tmp/cover.jpg")

    script = mock_script.call_args[0][0]
    assert "artwork 1 of t" in script
    assert "cover.jpg" in script
    assert 'persistent ID is "ABC123"' in script
    assert "read POSIX file" in script


# ── add_to_playlist ─────────────────────────────────────────────────────────


@patch(_PATCH)
def test_add_to_playlist_script(mock_script) -> None:
    """add_to_playlist creates/finds playlist and duplicates track."""
    add_to_playlist("My Playlist", "ABC123")

    script = mock_script.call_args[0][0]
    assert 'user playlist "My Playlist"' in script
    assert "make new user playlist" in script
    assert '"ABC123"' in script
    assert "duplicate t to p" in script


@patch(_PATCH)
def test_add_to_playlist_escapes_name(mock_script) -> None:
    """Playlist name with special chars is escaped."""
    add_to_playlist('Rock "Classics"', "ABC123")

    script = mock_script.call_args[0][0]
    assert 'Rock \\"Classics\\"' in script


# ── Batch functions ──────────────────────────────────────────────────────


@patch(_PATCH)
def test_update_tracks_batch_single_call(mock_run) -> None:
    """update_tracks_batch sends one AppleScript for all tracks."""

    update_tracks_batch(
        {
            "A1": {"genre": "Rock", "year": 2020},
            "A2": {"genre": "Pop"},
        }
    )

    mock_run.assert_called_once()
    script = mock_run.call_args[0][0]
    assert "A1" in script
    assert "A2" in script
    assert "Rock" in script


@patch(_PATCH)
def test_update_tracks_batch_empty(mock_run) -> None:
    """Empty updates → no AppleScript call."""

    update_tracks_batch({})
    mock_run.assert_not_called()


@patch(_PATCH)
def test_set_artwork_batch_single_call(mock_run) -> None:
    """set_artwork_batch sends one AppleScript for all tracks."""

    set_artwork_batch(["A1", "A2", "A3"], "/tmp/cover.jpg")

    mock_run.assert_called_once()
    script = mock_run.call_args[0][0]
    assert "A1" in script
    assert "A2" in script
    assert "repeat" in script


@patch(_PATCH)
def test_set_artwork_batch_empty(mock_run) -> None:
    """Empty list → no AppleScript call."""

    set_artwork_batch([], "/tmp/cover.jpg")
    mock_run.assert_not_called()


@patch(_PATCH)
def test_rebuild_playlist_single_call(mock_run) -> None:
    """rebuild_playlist sends one AppleScript (not N+1)."""

    rebuild_playlist("TestPlaylist", ["A1", "A2", "A3"])

    mock_run.assert_called_once()
    script = mock_run.call_args[0][0]
    assert "delete every track" in script
    assert "repeat" in script
    assert "A1" in script


@patch(_PATCH)
def test_rebuild_playlist_empty(mock_run) -> None:
    """Empty list → no AppleScript call."""

    rebuild_playlist("TestPlaylist", [])
    mock_run.assert_not_called()


@patch(_PATCH)
def test_list_playlists_parses_output(mock_run) -> None:
    """list_playlists parses AppleScript output."""

    mock_run.return_value = "Rock Mix:15\nWorkout:8\nChill:22\n"
    result = list_playlists()

    assert len(result) == 3
    assert result[0] == ("Chill", 22)  # sorted by name
    assert result[2] == ("Workout", 8)


@patch(_PATCH, return_value=None)
def test_list_playlists_empty(mock_run) -> None:
    """No playlists → empty list."""

    assert list_playlists() == []


@patch(_PATCH)
def test_get_playlist_tracks_returns_ids(mock_run) -> None:
    """get_playlist_tracks returns ordered IDs."""

    mock_run.return_value = "AAA\nBBB\nCCC\n"
    result = get_playlist_tracks("Test")

    assert result == ["AAA", "BBB", "CCC"]
