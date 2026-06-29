"""Apple Music service — read via iTunesLibrary, write via AppleScript.

Manages the in-memory library cache. Supports background scanning
for subsequent launches (menu displays immediately).
Write operations (import, update, delete, playlists) use AppleScript.
"""

import os
import subprocess
import threading
from collections.abc import Callable

from music_manager.core.models import LibraryEntry

# ── Constants ────────────────────────────────────────────────────────────────

RECO_FOLDER_NAME = "for me"


# ── Entry point ──────────────────────────────────────────────────────────────


class Apple:
    """In-memory cache of the Apple Music library with background scan support."""

    def __init__(self) -> None:
        self._cache: dict[str, LibraryEntry] = {}
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def scan(
        self, on_progress: Callable[[int, int], None] | None = None
    ) -> dict[str, LibraryEntry]:
        """Scan library (blocking). Returns {apple_id: LibraryEntry}."""
        self._cache = _scan_itunes_library(on_progress)
        self._ready.set()
        return self._cache

    def scan_background(self) -> None:
        """Launch library scan in a background thread."""
        self._ready.clear()
        self._thread = threading.Thread(target=self._background_scan, daemon=True)
        self._thread.start()

    def wait(self) -> None:
        """Block until background scan is complete."""
        self._ready.wait()

    def is_ready(self) -> bool:
        """Return True if scan is complete (or no scan running)."""
        return self._ready.is_set()

    def get_all(self) -> dict[str, LibraryEntry]:
        """Return cached library (shallow copy). Waits for background scan if needed."""
        self.wait()
        return dict(self._cache)

    def _background_scan(self) -> None:
        """Run scan and signal completion."""
        self._cache = _scan_itunes_library(None)
        self._ready.set()


def import_file(filepath: str) -> str:
    """Import an audio file into Apple Music. Returns the persistent apple_id.

    Raises RuntimeError if import fails.
    """
    abs_path = os.path.abspath(filepath)
    script = (
        'tell application "Music"\n'
        f'    set t to add POSIX file "{_esc(abs_path)}"\n'
        "    return persistent ID of t\n"
        "end tell"
    )
    apple_id = run_applescript(script)
    if not apple_id:
        raise RuntimeError(f"Import failed: no ID returned for {filepath}")
    return apple_id


def update_track(apple_id: str, fields: dict) -> None:
    """Update metadata fields on a track in Apple Music.

    Supported fields: title, artist, album, genre, year, track_number,
    total_tracks, disk_number, album_artist.
    """
    field_map = {
        "title": "name",
        "artist": "artist",
        "album": "album",
        "genre": "genre",
        "year": "year",
        "track_number": "track number",
        "total_tracks": "track count",
        "disk_number": "disc number",
        "album_artist": "album artist",
    }

    sets = []
    for key, value in fields.items():
        apple_field = field_map.get(key)
        if not apple_field:
            continue
        if isinstance(value, int):
            sets.append(f"set {apple_field} of t to {value}")
        else:
            sets.append(f'set {apple_field} of t to "{_esc(str(value))}"')

    if not sets:
        return

    body = "\n            ".join(sets)
    script = (
        'tell application "Music"\n'
        f'    set t to first track of library playlist 1 whose persistent ID is "{apple_id}"\n'
        f"    {body}\n"
        "end tell"
    )
    run_applescript(script)


def update_tracks_batch(updates: dict[str, dict]) -> None:
    """Update metadata fields on multiple tracks in a single AppleScript call.

    updates: {apple_id: {field: value, ...}, ...}
    """
    field_map = {
        "title": "name",
        "artist": "artist",
        "album": "album",
        "genre": "genre",
        "year": "year",
        "track_number": "track number",
        "total_tracks": "track count",
        "disk_number": "disc number",
        "album_artist": "album artist",
    }

    blocks = []
    for apple_id, fields in updates.items():
        sets = []
        for key, value in fields.items():
            apple_field = field_map.get(key)
            if not apple_field:
                continue
            if isinstance(value, int):
                sets.append(f"set {apple_field} of t to {value}")
            else:
                sets.append(f'set {apple_field} of t to "{_esc(str(value))}"')
        if sets:
            body = "\n            ".join(sets)
            blocks.append(
                "    try\n"
                "        set t to first track of library playlist 1"
                f' whose persistent ID is "{_esc(apple_id)}"\n'
                f"        {body}\n"
                "    end try"
            )

    if not blocks:
        return

    script = 'tell application "Music"\n' + "\n".join(blocks) + "\nend tell"
    run_applescript(script)


def delete_tracks(apple_ids: list[str]) -> int:
    """Delete tracks from Apple Music by persistent ID. Returns count deleted."""
    if not apple_ids:
        return 0

    id_list = ", ".join(f'"{_esc(aid)}"' for aid in apple_ids)
    script = (
        'tell application "Music"\n'
        f"    set idsToDelete to {{{id_list}}}\n"
        "    set deletedCount to 0\n"
        "    repeat with targetId in idsToDelete\n"
        "        try\n"
        "            delete (first track of library playlist 1"
        " whose persistent ID is targetId)\n"
        "            set deletedCount to deletedCount + 1\n"
        "        end try\n"
        "    end repeat\n"
        "    return deletedCount\n"
        "end tell"
    )
    result = run_applescript(script)
    try:
        return int(result) if result else 0
    except ValueError:
        return 0


def set_artwork(apple_id: str, image_path: str) -> None:
    """Set artwork on a track in Apple Music from an image file."""
    abs_path = os.path.abspath(image_path)
    fmt = "«class PNG »" if abs_path.endswith(".png") else "«class JPEG»"
    script = (
        'tell application "Music"\n'
        f'    set t to first track of library playlist 1 whose persistent ID is "{apple_id}"\n'
        f'    set imgData to (read POSIX file "{_esc(abs_path)}" as {fmt})\n'
        "    set data of artwork 1 of t to imgData\n"
        "end tell"
    )
    run_applescript(script)


def set_artwork_batch(apple_ids: list[str], image_path: str) -> None:
    """Set artwork on multiple tracks in a single AppleScript call."""
    if not apple_ids:
        return
    abs_path = os.path.abspath(image_path)
    fmt = "«class PNG »" if abs_path.endswith(".png") else "«class JPEG»"
    id_list = ", ".join(f'"{_esc(aid)}"' for aid in apple_ids)
    script = (
        'tell application "Music"\n'
        f'    set imgData to (read POSIX file "{_esc(abs_path)}" as {fmt})\n'
        f"    set idsToSet to {{{id_list}}}\n"
        "    repeat with targetId in idsToSet\n"
        "        try\n"
        "            set t to first track of library playlist 1"
        " whose persistent ID is targetId\n"
        "            set data of artwork 1 of t to imgData\n"
        "        end try\n"
        "    end repeat\n"
        "end tell"
    )
    run_applescript(script)


def apple_ids_exist(apple_ids: list[str]) -> set[str]:
    """Return the subset of ``apple_ids`` that still exist in Apple Music.

    Used by the widget CLI to detect orphaned ``tracks.json`` entries (tracks
    the user deleted from the Apple Music app but that we didn't yet purge
    from our store). A single AppleScript call walks the candidate IDs via
    ``whose persistent ID is …`` which is fast for small lists.

    Empty input returns an empty set without spawning a subprocess.
    """
    if not apple_ids:
        return set()
    id_list = ", ".join(f'"{_esc(aid)}"' for aid in apple_ids)
    script = (
        'tell application "Music"\n'
        f"    set targetIds to {{{id_list}}}\n"
        '    set output to ""\n'
        "    repeat with tid in targetIds\n"
        "        try\n"
        "            set _t to first track of library playlist 1"
        " whose persistent ID is (tid as string)\n"
        '            set output to output & (tid as string) & linefeed\n'
        "        end try\n"
        "    end repeat\n"
        "    return output\n"
        "end tell"
    )
    result = run_applescript(script)
    if not result:
        return set()
    return {line.strip() for line in result.split("\n") if line.strip()}


def set_playlist_artwork(playlist_name: str, image_path: str) -> bool:
    """Best-effort: set the artwork of a user playlist from an image file.

    Apple Music's AppleScript surface for playlist artwork isn't fully
    documented and behaviour shifts between macOS releases; callers must
    treat a False return as "playlist exists but artwork couldn't be set"
    rather than a hard error. The playlist itself is untouched on failure.
    """
    if not playlist_name or not image_path:
        return False
    abs_path = os.path.abspath(image_path)
    if not os.path.isfile(abs_path):
        return False
    fmt = "«class PNG »" if abs_path.endswith(".png") else "«class JPEG»"
    escaped_name = _esc(playlist_name)
    script = (
        'tell application "Music"\n'
        f'    set p to user playlist "{escaped_name}"\n'
        f'    set imgData to (read POSIX file "{_esc(abs_path)}" as {fmt})\n'
        "    try\n"
        "        delete artworks of p\n"
        "    end try\n"
        "    tell p\n"
        "        make new artwork at end of artworks"
        " with properties {data:imgData}\n"
        "    end tell\n"
        "end tell"
    )
    return run_applescript(script) is not None


def get_playlist_membership(
    apple_id: str, exclude_folder: str | None = None
) -> list[tuple[str, list[str]]]:
    """Find which user playlists contain a track.

    Returns ``[(playlist_name, [ordered_persistent_ids]), ...]``. If
    ``exclude_folder`` is given, playlists whose parent folder bears
    that name are omitted (used to skip the ``for me`` folder when
    detecting adoption — moving a track between two ``for me``
    sub-playlists is not adoption into a user playlist).
    """
    detailed = get_playlist_membership_detailed(apple_id)
    return [
        (name, ids)
        for name, parent, ids in detailed
        if not (exclude_folder and parent == exclude_folder)
    ]


def get_playlist_membership_detailed(
    apple_id: str,
) -> list[tuple[str, str, list[str]]]:
    """Like ``get_playlist_membership`` but exposes each playlist's parent folder.

    Returns ``[(playlist_name, parent_folder_or_empty, [ordered_ids]), ...]``.
    Useful to distinguish moves between sub-playlists of the same folder
    from genuine adoption into a user playlist.
    """
    script = (
        'tell application "Music"\n'
        '    set output to ""\n'
        "    repeat with p in user playlists\n"
        "        if smart of p is false then\n"
        "            try\n"
        "                set t to first track of p"
        f' whose persistent ID is "{_esc(apple_id)}"\n'
        '                set parentName to ""\n'
        "                try\n"
        "                    set parentName to name of parent of p\n"
        "                end try\n"
        "                set pName to name of p\n"
        '                set output to output & "PLAYLIST:" & pName'
        ' & "|||" & parentName & linefeed\n'
        "                repeat with tk in tracks of p\n"
        "                    set output to output"
        " & persistent ID of tk & linefeed\n"
        "                end repeat\n"
        "            end try\n"
        "        end if\n"
        "    end repeat\n"
        "    return output\n"
        "end tell"
    )
    result = run_applescript(script)
    if not result:
        return []

    playlists: list[tuple[str, str, list[str]]] = []
    current_name = ""
    current_parent = ""
    current_ids: list[str] = []

    def flush() -> None:
        if not current_name:
            return
        playlists.append((current_name, current_parent, list(current_ids)))

    for line in result.strip().splitlines():
        if line.startswith("PLAYLIST:"):
            flush()
            header = line[9:]
            parts = header.split("|||", 1)
            current_name = parts[0]
            current_parent = parts[1] if len(parts) > 1 else ""
            current_ids = []
        else:
            current_ids.append(line.strip())
    flush()

    return playlists


def list_playlists_with_tracks() -> list[tuple[str, str, list[str]]]:
    """Scan every non-smart user playlist with its track list.

    Returns ``[(playlist_name, parent_folder_or_empty, [ordered_ids]), ...]``.
    A single AppleScript call streams the whole library — used by the
    duplicates flow to detect identical persistent IDs appearing twice
    within the same playlist.
    """
    script = (
        'tell application "Music"\n'
        '    set output to ""\n'
        "    repeat with p in user playlists\n"
        "        if smart of p is false then\n"
        "            set pName to name of p\n"
        '            set parentName to ""\n'
        "            try\n"
        "                set parentName to name of parent of p\n"
        "            end try\n"
        '            set output to output & "PLAYLIST:" & pName'
        ' & "|||" & parentName & linefeed\n'
        "            repeat with t in tracks of p\n"
        "                set output to output"
        " & persistent ID of t & linefeed\n"
        "            end repeat\n"
        "        end if\n"
        "    end repeat\n"
        "    return output\n"
        "end tell"
    )
    result = run_applescript(script)
    if not result:
        return []

    playlists: list[tuple[str, str, list[str]]] = []
    current_name = ""
    current_parent = ""
    current_ids: list[str] = []

    def flush() -> None:
        if not current_name:
            return
        playlists.append((current_name, current_parent, list(current_ids)))

    for line in result.strip().splitlines():
        if line.startswith("PLAYLIST:"):
            flush()
            header = line[9:]
            parts = header.split("|||", 1)
            current_name = parts[0]
            current_parent = parts[1] if len(parts) > 1 else ""
            current_ids = []
        else:
            stripped = line.strip()
            if stripped:
                current_ids.append(stripped)
    flush()

    return playlists


def rebuild_playlist(playlist_name: str, apple_ids: list[str]) -> None:
    """Clear a playlist and re-add tracks in the given order."""
    if not apple_ids:
        return

    escaped_name = _esc(playlist_name)
    id_list = ", ".join(f'"{_esc(aid)}"' for aid in apple_ids)
    script = (
        'tell application "Music"\n'
        f'    set p to user playlist "{escaped_name}"\n'
        "    delete every track of p\n"
        f"    set idsToAdd to {{{id_list}}}\n"
        "    repeat with targetId in idsToAdd\n"
        "        try\n"
        "            set t to first track of library playlist 1"
        " whose persistent ID is targetId\n"
        "            duplicate t to p\n"
        "        end try\n"
        "    end repeat\n"
        "end tell"
    )
    run_applescript(script)


def list_playlists(exclude_folder: str | None = None) -> list[tuple[str, int]]:
    """List user playlists. Returns ``[(name, track_count), ...]`` sorted by name.

    If ``exclude_folder`` is given, playlists whose parent folder bears
    that name are omitted from the result.
    """
    script = (
        'tell application "Music"\n'
        '    set output to ""\n'
        "    repeat with p in user playlists\n"
        "        if smart of p is false then\n"
        '            set parentName to ""\n'
        "            try\n"
        "                set parentName to name of parent of p\n"
        "            end try\n"
        "            set pName to name of p\n"
        "            set tCount to count of tracks of p\n"
        '            set output to output & pName & "|||" & tCount'
        ' & "|||" & parentName & linefeed\n'
        "        end if\n"
        "    end repeat\n"
        "    return output\n"
        "end tell"
    )
    result = run_applescript(script)
    if not result:
        return []

    playlists: list[tuple[str, int]] = []
    for line in result.strip().splitlines():
        parts = line.split("|||")
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        try:
            count = int(parts[1].strip())
        except ValueError:
            continue
        parent_name = parts[2].strip() if len(parts) > 2 else ""
        if exclude_folder and parent_name == exclude_folder:
            continue
        playlists.append((name, count))
    return sorted(playlists, key=lambda x: x[0].lower())


def ensure_folder_playlist(folder_name: str) -> None:
    """Create a folder playlist if it doesn't exist. Idempotent."""
    if not folder_name:
        return
    escaped = _esc(folder_name)
    script = (
        'tell application "Music"\n'
        "    try\n"
        f'        folder playlist "{escaped}"\n'
        "    on error\n"
        f'        make new folder playlist with properties {{name:"{escaped}"}}\n'
        "    end try\n"
        "end tell"
    )
    run_applescript(script)


def user_playlist_collides_with_folder(folder_name: str) -> bool:
    """Return True if a non-folder user playlist already bears ``folder_name``.

    Apple Music allows several items to share a name, so if the user has a
    regular playlist called ``for me``, creating the folder of the same name
    would leave two coexisting items and confuse navigation. Callers should
    surface this state to the user (warn + suggest renaming) instead of
    silently creating the duplicate folder.
    """
    if not folder_name:
        return False
    escaped = _esc(folder_name)
    script = (
        'tell application "Music"\n'
        "    set found to false\n"
        f'    repeat with p in (user playlists whose name is "{escaped}")\n'
        "        try\n"
        # If the item is itself a folder playlist, ``folder playlist`` of
        # the same name resolves to the same ref — class check is the
        # safest portable way.
        "            if class of p is not folder playlist then\n"
        "                set found to true\n"
        "                exit repeat\n"
        "            end if\n"
        "        end try\n"
        "    end repeat\n"
        "    return found as string\n"
        "end tell"
    )
    try:
        result = run_applescript(script)
    except Exception:  # noqa: BLE001
        return False
    return bool(result) and result.strip().lower() == "true"


def playlist_exists_in_folder(folder_name: str, playlist_name: str) -> bool:
    """Return True if a playlist named ``playlist_name`` lives inside ``folder_name``."""
    if not folder_name or not playlist_name:
        return False
    script = (
        'tell application "Music"\n'
        "    set found to false\n"
        f'    repeat with p in (user playlists whose name is "{_esc(playlist_name)}")\n'
        "        try\n"
        f'            if name of parent of p is "{_esc(folder_name)}" then\n'
        "                set found to true\n"
        "                exit repeat\n"
        "            end if\n"
        "        end try\n"
        "    end repeat\n"
        "    return found as string\n"
        "end tell"
    )
    try:
        result = run_applescript(script)
    except Exception:  # noqa: BLE001
        return False
    return bool(result) and result.strip().lower() == "true"


def get_playlist_tracks_in_folder(
    folder_name: str, playlist_name: str
) -> list[str]:
    """Return persistent IDs of tracks in ``folder_name/playlist_name``, in order."""
    if not folder_name or not playlist_name:
        return []
    script = (
        'tell application "Music"\n'
        '    set output to ""\n'
        f'    repeat with p in (user playlists whose name is "{_esc(playlist_name)}")\n'
        "        try\n"
        f'            if name of parent of p is "{_esc(folder_name)}" then\n'
        "                repeat with t in tracks of p\n"
        "                    set output to output"
        " & persistent ID of t & linefeed\n"
        "                end repeat\n"
        "                exit repeat\n"
        "            end if\n"
        "        end try\n"
        "    end repeat\n"
        "    return output\n"
        "end tell"
    )
    result = run_applescript(script)
    if not result:
        return []
    return [line.strip() for line in result.strip().splitlines() if line.strip()]


def add_to_playlist_in_folder(
    folder_name: str,
    playlist_name: str,
    apple_ids: list[str] | str,
) -> int:
    """Sync ``apple_ids`` into ``folder_name/playlist_name``.

    Creates the folder if missing, then the playlist inside the folder
    if missing. Preserves manually added tracks (same semantics as
    ``add_to_playlist``). Returns the count of NEW tracks (not already
    in the playlist before this call).
    """
    if isinstance(apple_ids, str):
        apple_ids = [apple_ids]
    if not apple_ids or not folder_name or not playlist_name:
        return 0

    escaped_folder = _esc(folder_name)
    escaped_playlist = _esc(playlist_name)
    id_list = ", ".join(f'"{_esc(aid)}"' for aid in apple_ids)

    script = (
        'tell application "Music"\n'
        # 1. Locate or create the folder.
        "    set folderRef to missing value\n"
        "    try\n"
        f'        set folderRef to folder playlist "{escaped_folder}"\n'
        "    on error\n"
        f'        set folderRef to make new folder playlist '
        f'with properties {{name:"{escaped_folder}"}}\n'
        "    end try\n"
        # 2. Find an existing playlist of the right name *inside* the target
        #    folder. Same-named playlists living elsewhere (e.g. the user's
        #    original playlist at the root) MUST be left untouched — the
        #    recommendation flow creates a separate sibling in ``for me/``.
        "    set p to missing value\n"
        f'    repeat with pl in (user playlists whose name is "{escaped_playlist}")\n'
        '        set parentName to ""\n'
        "        try\n"
        "            set parentName to name of parent of pl\n"
        "        end try\n"
        f'        if parentName is "{escaped_folder}" then\n'
        "            set p to pl\n"
        "            exit repeat\n"
        "        end if\n"
        "    end repeat\n"
        # 3. Still nothing → create a fresh playlist and move it under the
        #    folder. ``set parent of p to ...`` is silently a no-op on recent
        #    Music.app, ``move`` is the documented verb that actually works.
        "    if p is missing value then\n"
        f'        set p to make new user playlist '
        f'with properties {{name:"{escaped_playlist}"}}\n'
        "        try\n"
        "            move p to folderRef\n"
        "        end try\n"
        "    end if\n"
        "    set existingIDs to {}\n"
        "    try\n"
        "        repeat with trk in tracks of p\n"
        "            set end of existingIDs to persistent ID of trk\n"
        "        end repeat\n"
        "    end try\n"
        f"    set csvIDs to {{{id_list}}}\n"
        "    set manualIDs to {}\n"
        "    repeat with eid in existingIDs\n"
        "        if csvIDs does not contain (eid as string) then\n"
        "            set end of manualIDs to eid\n"
        "        end if\n"
        "    end repeat\n"
        "    try\n"
        "        delete every track of p\n"
        "    end try\n"
        "    set addedCount to 0\n"
        "    repeat with targetId in csvIDs\n"
        "        try\n"
        "            set t to first track of library playlist 1"
        " whose persistent ID is targetId\n"
        "            duplicate t to p\n"
        "            if existingIDs does not contain (targetId as string) then\n"
        "                set addedCount to addedCount + 1\n"
        "            end if\n"
        "        end try\n"
        "    end repeat\n"
        "    repeat with manualId in manualIDs\n"
        "        try\n"
        "            set t to first track of library playlist 1"
        " whose persistent ID is manualId\n"
        "            duplicate t to p\n"
        "        end try\n"
        "    end repeat\n"
        "    return addedCount\n"
        "end tell"
    )
    result = run_applescript(script)
    try:
        return int(result) if result else 0
    except ValueError:
        return 0


def get_playlist_tracks(playlist_name: str) -> list[str]:
    """Get all track persistent IDs from a playlist, in order."""
    script = (
        'tell application "Music"\n'
        f'    set p to user playlist "{_esc(playlist_name)}"\n'
        '    set output to ""\n'
        "    repeat with t in tracks of p\n"
        "        set output to output & persistent ID of t & linefeed\n"
        "    end repeat\n"
        "    return output\n"
        "end tell"
    )
    result = run_applescript(script)
    if not result:
        return []
    return [line.strip() for line in result.strip().splitlines() if line.strip()]


def add_to_playlist(playlist_name: str, apple_ids: list[str] | str) -> int:
    """Sync CSV tracks into a playlist, preserving manually added tracks.

    1. Collect IDs already in the playlist
    2. Identify manual tracks (in playlist but not in our list)
    3. Clear playlist
    4. Re-add CSV tracks in order
    5. Re-add manual tracks at the end (preserved)
    Returns count of NEW tracks (not previously in the playlist).
    """
    if isinstance(apple_ids, str):
        apple_ids = [apple_ids]
    if not apple_ids:
        return 0

    escaped_name = _esc(playlist_name)
    id_list = ", ".join(f'"{_esc(aid)}"' for aid in apple_ids)

    script = (
        'tell application "Music"\n'
        "    set p to null\n"
        "    try\n"
        f'        set p to user playlist "{escaped_name}"\n'
        "    on error\n"
        f"        set p to make new user playlist with properties "
        f'{{name:"{escaped_name}"}}\n'
        "    end try\n"
        # Collect existing IDs
        "    set existingIDs to {}\n"
        "    try\n"
        "        repeat with trk in tracks of p\n"
        "            set end of existingIDs to persistent ID of trk\n"
        "        end repeat\n"
        "    end try\n"
        # Find manual tracks (in playlist but not in our CSV list)
        f"    set csvIDs to {{{id_list}}}\n"
        "    set manualIDs to {}\n"
        "    repeat with eid in existingIDs\n"
        "        if csvIDs does not contain (eid as string) then\n"
        "            set end of manualIDs to eid\n"
        "        end if\n"
        "    end repeat\n"
        # Clear playlist
        "    try\n"
        "        delete every track of p\n"
        "    end try\n"
        # Re-add CSV tracks in order
        "    set addedCount to 0\n"
        "    repeat with targetId in csvIDs\n"
        "        try\n"
        "            set t to first track of library playlist 1"
        " whose persistent ID is targetId\n"
        "            duplicate t to p\n"
        "            if existingIDs does not contain (targetId as string) then\n"
        "                set addedCount to addedCount + 1\n"
        "            end if\n"
        "        end try\n"
        "    end repeat\n"
        # Re-add manual tracks at the end (preserved)
        "    repeat with manualId in manualIDs\n"
        "        try\n"
        "            set t to first track of library playlist 1"
        " whose persistent ID is manualId\n"
        "            duplicate t to p\n"
        "        end try\n"
        "    end repeat\n"
        "    return addedCount\n"
        "end tell"
    )
    result = run_applescript(script)
    try:
        return int(result) if result else 0
    except ValueError:
        return 0


# ── Public helpers ───────────────────────────────────────────────────────────


def run_applescript(script: str) -> str | None:
    """Execute an AppleScript and return stdout. Returns None on error."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def open_url_over_music(url: str) -> None:
    """Open a URL in Safari, filling the available screen space (dock-aware)."""
    safe_url = _esc(url)
    run_applescript(f'''
        -- Screen dimensions
        tell application "Finder"
            set db to bounds of window of desktop
            set sw to item 3 of db
            set sh to item 4 of db
        end tell

        -- Dock height
        set dockH to 0
        try
            set dh to do shell script "defaults read com.apple.dock autohide 2>/dev/null || echo 0"
            if dh is "0" then
                set ds to (do shell script ¬
                    "defaults read com.apple.dock tilesize 2>/dev/null || echo 48") as integer
                set dockH to ds + 16
            end if
        end try

        set mb to 25
        set safariBounds to {{0, mb, sw, sh - dockH}}

        -- Open URL in Safari (reuse window, no start tab)
        tell application "Safari"
            if (count of windows) > 0 then
                set URL of current tab of front window to "{safe_url}"
            else
                make new document with properties {{URL:"{safe_url}"}}
            end if
            delay 0.5
            if (count of windows) > 0 then
                set bounds of front window to safariBounds
            end if
            activate
        end tell
    ''')


def _esc(value: str) -> str:
    """Escape a string for safe inclusion in AppleScript."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\0", "")
    )


# ── Private Functions ────────────────────────────────────────────────────────


def _scan_itunes_library(
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, LibraryEntry]:
    """Fast scan using iTunesLibrary.framework (PyObjC)."""
    import objc  # noqa: PLC0415

    objc.loadBundle(  # type: ignore[attr-defined]
        "iTunesLibrary",
        {},
        bundle_path="/System/Library/Frameworks/iTunesLibrary.framework",
    )
    ITLibrary = objc.lookUpClass("ITLibrary")  # type: ignore[attr-defined]
    _MEDIA_KIND_SONG = 2

    library = ITLibrary.alloc().initWithAPIVersion_error_("1.1", None)
    if library is None:
        raise RuntimeError("Failed to initialize ITLibrary")

    all_items = [item for item in library.allMediaItems() if item.mediaKind() == _MEDIA_KIND_SONG]
    total = len(all_items)
    result: dict[str, LibraryEntry] = {}

    for index, item in enumerate(all_items):
        album_obj = item.album()
        artist_obj = item.artist()
        location = item.location()
        apple_id = format(item.persistentID(), "016X")  # 16-char hex, matches AppleScript

        last_played_date = item.lastPlayedDate()
        added_date = item.addedDate()

        result[apple_id] = LibraryEntry(
            apple_id=apple_id,
            title=str(item.title() or ""),
            artist=str(artist_obj.name()) if artist_obj else "",
            album=str(album_obj.title()) if album_obj else "",
            year=str(item.year()) if item.year() else "",
            genre=str(item.genre() or ""),
            track_number=item.trackNumber() or None,
            total_tracks=album_obj.trackCount() if album_obj else None,
            disk_number=item.albumDiscNumber() or 0,
            album_artist=str(album_obj.albumArtist() or "") if album_obj else "",
            duration=item.totalTime() / 1000.0 if item.totalTime() else 0.0,
            explicit=bool(item.lyricsContentRating()),
            has_artwork=bool(item.hasArtworkAvailable()),
            file_path=str(location.path()) if location else "",
            loved=bool(item.isLoved()) if hasattr(item, "isLoved") else False,
            play_count=int(item.playCount() or 0),
            last_played=str(last_played_date.description()) if last_played_date else "",
            added_date=str(added_date.description()) if added_date else "",
        )

        if on_progress and index % 50 == 0:
            on_progress(index + 1, total)

    if on_progress:
        on_progress(total, total)

    return result
