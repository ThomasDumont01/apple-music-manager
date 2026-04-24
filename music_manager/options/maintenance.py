"""Maintenance operations (§14)."""

import os
import shutil

from music_manager.core.io import save_json
from music_manager.services.apple import delete_tracks
from music_manager.services.tracks import Tracks

# ── Entry point ──────────────────────────────────────────────────────────────


def reset_failed(tracks_store: Tracks) -> int:
    """Reset all failed tracks to pending (status=None). Returns count reset."""
    count = 0
    for apple_id, entry in list(tracks_store.all().items()):
        if entry.get("status") == "failed":
            tracks_store.update(apple_id, {"status": None, "fail_reason": ""})
            count += 1
    if count > 0:
        tracks_store.save()
    return count


def clear_preferences(preferences_path: str) -> None:
    """Clear all user preferences."""
    save_json(preferences_path, {})


def revert_imports(tracks_store: Tracks) -> int:
    """Delete all imported tracks from Apple Music and tracks.json. Returns count."""
    to_delete = []
    for apple_id, entry in list(tracks_store.all().items()):
        if entry.get("origin") == "imported" and entry.get("status") == "done":
            to_delete.append(apple_id)

    if to_delete:
        delete_tracks(to_delete)
        for apple_id in to_delete:
            tracks_store.remove(apple_id)
        tracks_store.save()

    return len(to_delete)


_DATA_ITEMS = (".data", ".tmp", "playlists", "raccourcis", "requetes.csv")


def move_data(old_root: str, new_root: str) -> bool:
    """Move project data from old_root to new_root. Returns True on success."""
    from music_manager.core.config import save_config  # noqa: PLC0415

    if not os.path.isdir(old_root):
        return False
    if os.path.realpath(old_root) == os.path.realpath(new_root):
        return False

    os.makedirs(new_root, exist_ok=True)

    # Move known data items
    for name in _DATA_ITEMS:
        src = os.path.join(old_root, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(new_root, name)
        if os.path.exists(dst):
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            else:
                os.remove(dst)
        shutil.move(src, dst)

    # Move any other CSV files at root level
    for name in os.listdir(old_root):
        if name.lower().endswith(".csv"):
            src = os.path.join(old_root, name)
            dst = os.path.join(new_root, name)
            if os.path.exists(dst):
                os.remove(dst)
            shutil.move(src, dst)

    save_config({"data_root": new_root})
    return True


def delete_all(data_root: str) -> bool:
    """Delete all Music Manager data (.data/ and config). Returns True if deleted."""
    from music_manager.core.config import CONFIG_DIR  # noqa: PLC0415

    deleted = False
    data_dir = os.path.join(data_root, ".data")
    if os.path.isdir(data_dir):
        shutil.rmtree(data_dir)
        deleted = True
    if os.path.isdir(CONFIG_DIR):
        shutil.rmtree(CONFIG_DIR)
        deleted = True
    return deleted
