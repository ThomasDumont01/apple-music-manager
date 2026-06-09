"""Unit tests for services/apple.py — AppleScript logic (portable, no macOS required)."""

from unittest.mock import patch

import pytest

from music_manager.services.apple import (
    RECO_FOLDER_NAME,
    _esc,
    add_to_playlist,
    add_to_playlist_in_folder,
    delete_tracks,
    ensure_folder_playlist,
    get_playlist_membership,
    get_playlist_membership_detailed,
    get_playlist_tracks,
    get_playlist_tracks_in_folder,
    import_file,
    list_playlists,
    playlist_exists_in_folder,
    rebuild_playlist,
    set_artwork,
    set_artwork_batch,
    update_track,
    update_tracks_batch,
    user_playlist_collides_with_folder,
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

    mock_run.return_value = "Rock Mix|||15|||\nWorkout|||8|||\nChill|||22|||\n"
    result = list_playlists()

    assert len(result) == 3
    assert result[0] == ("Chill", 22)  # sorted by name
    assert result[2] == ("Workout", 8)


@patch(_PATCH, return_value=None)
def test_list_playlists_empty(mock_run) -> None:
    """No playlists → empty list."""

    assert list_playlists() == []


@patch(_PATCH)
def test_list_playlists_exclude_folder_filters_children(mock_run) -> None:
    """Playlists whose parent matches ``exclude_folder`` are dropped."""

    mock_run.return_value = (
        "Workout|||8|||\n"
        "library|||20|||for me\n"
        "rock|||12|||for me\n"
        "Favorites|||5|||\n"
    )
    result = list_playlists(exclude_folder="for me")
    names = [name for name, _count in result]
    assert names == ["Favorites", "Workout"]


@patch(_PATCH)
def test_list_playlists_legacy_no_parent_column_still_parses(mock_run) -> None:
    """Old payloads missing the parent column still parse (no filter applied)."""

    mock_run.return_value = "Rock|||15|||\nWorkout|||8|||\n"
    result = list_playlists()
    assert {name for name, _ in result} == {"Rock", "Workout"}


@patch(_PATCH)
def test_get_playlist_tracks_returns_ids(mock_run) -> None:
    """get_playlist_tracks returns ordered IDs."""

    mock_run.return_value = "AAA\nBBB\nCCC\n"
    result = get_playlist_tracks("Test")

    assert result == ["AAA", "BBB", "CCC"]


# ── get_playlist_membership (folder-aware) ──────────────────────────────────


@patch(_PATCH)
def test_get_playlist_membership_detailed_returns_parent(mock_run) -> None:
    """The detailed variant exposes the parent folder for each playlist."""
    mock_run.return_value = (
        "PLAYLIST:My Favs|||\n"
        "T1\n"
        "PLAYLIST:library|||for me\n"
        "T1\n"
    )
    result = get_playlist_membership_detailed("T1")
    assert ("My Favs", "", ["T1"]) in result
    assert ("library", "for me", ["T1"]) in result


@patch(_PATCH, return_value=None)
def test_get_playlist_membership_detailed_empty(mock_run) -> None:
    assert get_playlist_membership_detailed("ANY") == []


@patch(_PATCH)
def test_get_playlist_membership_parses_parent(mock_run) -> None:
    mock_run.return_value = (
        "PLAYLIST:My Favs|||\n"
        "T1\n"
        "T2\n"
        "PLAYLIST:library|||for me\n"
        "T1\n"
        "T3\n"
    )
    result = get_playlist_membership("T1")
    assert ("My Favs", ["T1", "T2"]) in result
    assert ("library", ["T1", "T3"]) in result


@patch(_PATCH)
def test_get_playlist_membership_exclude_folder(mock_run) -> None:
    mock_run.return_value = (
        "PLAYLIST:My Favs|||\n"
        "T1\n"
        "PLAYLIST:library|||for me\n"
        "T1\n"
        "PLAYLIST:rock|||for me\n"
        "T1\n"
    )
    result = get_playlist_membership("T1", exclude_folder="for me")
    names = [name for name, _ in result]
    assert names == ["My Favs"]


@patch(_PATCH, return_value=None)
def test_get_playlist_membership_empty(mock_run) -> None:
    assert get_playlist_membership("ANY") == []


# ── Folder + playlist-in-folder helpers ────────────────────────────────────


def test_reco_folder_name_constant() -> None:
    assert RECO_FOLDER_NAME == "for me"


@patch(_PATCH)
def test_ensure_folder_playlist_invokes_script(mock_run) -> None:
    ensure_folder_playlist("for me")
    assert mock_run.called
    script = mock_run.call_args[0][0]
    assert "folder playlist" in script
    assert '"for me"' in script
    assert "make new folder playlist" in script


@patch(_PATCH)
def test_ensure_folder_playlist_empty_name_noop(mock_run) -> None:
    ensure_folder_playlist("")
    mock_run.assert_not_called()


@patch(_PATCH)
def test_playlist_exists_in_folder_true(mock_run) -> None:
    mock_run.return_value = "true"
    assert playlist_exists_in_folder("for me", "library") is True


@patch(_PATCH)
def test_playlist_exists_in_folder_false(mock_run) -> None:
    mock_run.return_value = "false"
    assert playlist_exists_in_folder("for me", "library") is False


@patch(_PATCH, return_value=None)
def test_playlist_exists_in_folder_none(mock_run) -> None:
    assert playlist_exists_in_folder("for me", "library") is False


def test_playlist_exists_in_folder_empty_args() -> None:
    assert playlist_exists_in_folder("", "library") is False
    assert playlist_exists_in_folder("for me", "") is False


@patch(_PATCH)
def test_get_playlist_tracks_in_folder_returns_ids(mock_run) -> None:
    mock_run.return_value = "T1\nT2\nT3\n"
    assert get_playlist_tracks_in_folder("for me", "library") == ["T1", "T2", "T3"]


@patch(_PATCH, return_value=None)
def test_get_playlist_tracks_in_folder_empty(mock_run) -> None:
    assert get_playlist_tracks_in_folder("for me", "library") == []


def test_get_playlist_tracks_in_folder_empty_args() -> None:
    assert get_playlist_tracks_in_folder("", "library") == []
    assert get_playlist_tracks_in_folder("for me", "") == []


@patch(_PATCH)
def test_add_to_playlist_in_folder_emits_script(mock_run) -> None:
    mock_run.return_value = "2"
    count = add_to_playlist_in_folder("for me", "library", ["AP1", "AP2"])
    assert count == 2
    script = mock_run.call_args[0][0]
    assert '"for me"' in script
    assert '"library"' in script
    assert '"AP1"' in script
    assert '"AP2"' in script
    assert "make new folder playlist" in script
    assert "make new user playlist" in script
    # Use ``move`` (the documented verb) rather than ``set parent of`` so the
    # playlist actually lands inside the folder on recent Music.app.
    assert "move p to folderRef" in script
    # Orphan recovery: a same-named playlist created earlier at the library
    # root must be moved into the folder rather than left as a duplicate.
    assert "move pOrphan to folderRef" in script


@patch(_PATCH)
def test_add_to_playlist_in_folder_accepts_single_id(mock_run) -> None:
    mock_run.return_value = "1"
    count = add_to_playlist_in_folder("for me", "library", "AP1")
    assert count == 1


@patch(_PATCH)
def test_add_to_playlist_in_folder_empty_inputs_noop(mock_run) -> None:
    assert add_to_playlist_in_folder("", "library", ["AP1"]) == 0
    assert add_to_playlist_in_folder("for me", "", ["AP1"]) == 0
    assert add_to_playlist_in_folder("for me", "library", []) == 0
    mock_run.assert_not_called()


@patch(_PATCH, return_value=None)
def test_add_to_playlist_in_folder_script_failure_returns_zero(mock_run) -> None:
    assert add_to_playlist_in_folder("for me", "library", ["AP1"]) == 0


@patch(_PATCH)
def test_add_to_playlist_in_folder_non_numeric_returns_zero(mock_run) -> None:
    mock_run.return_value = "garbage"
    assert add_to_playlist_in_folder("for me", "library", ["AP1"]) == 0


# ── user_playlist_collides_with_folder ─────────────────────────────────────


@patch(_PATCH, return_value="true")
def test_collision_detected_when_script_returns_true(mock_run) -> None:
    assert user_playlist_collides_with_folder("for me") is True


@patch(_PATCH, return_value="false")
def test_collision_negative_when_script_returns_false(mock_run) -> None:
    assert user_playlist_collides_with_folder("for me") is False


@patch(_PATCH, side_effect=RuntimeError("AppleScript blocked"))
def test_collision_defensive_on_script_failure(mock_run) -> None:
    """A failing AppleScript must not crash the caller — return False."""
    assert user_playlist_collides_with_folder("for me") is False


def test_collision_empty_name_returns_false() -> None:
    assert user_playlist_collides_with_folder("") is False
