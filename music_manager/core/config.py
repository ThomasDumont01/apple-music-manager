"""Application configuration — load/save from ~/.config/music_manager/config.json.

Stores user preferences that persist across sessions:
- data_root: path to the user's data folder
- setup_done: whether the first-launch scan has been completed
"""

import json
import os
import threading

from music_manager.core.io import save_json

# ── Constants ────────────────────────────────────────────────────────────────

# save_config() is a read-modify-write: the recommendation feed builds its
# sections in parallel and any worker can refresh the Spotify token, so
# several threads reach this at once. Without the lock they crash on the
# shared temp file or silently drop each other's keys.
_CONFIG_LOCK = threading.RLock()

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "music_manager")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

_DEFAULTS = {
    "data_root": "",
    "setup_done": False,
    "youtube_cookies": False,
    # Spotify OAuth (PKCE) — populated by `spotify-login`.
    # Tokens are chmod'd 600 by `services/spotify.save_tokens`.
    "spotify_client_id": "",
    "spotify_access_token": "",
    "spotify_refresh_token": "",
    "spotify_token_expiry": 0.0,
}


# ── Entry point ──────────────────────────────────────────────────────────────


def load_config() -> dict[str, object]:
    """Load configuration from disk. Returns defaults merged with saved values.

    If the file is missing or corrupt, returns a fresh copy of defaults.
    """
    if not os.path.isfile(CONFIG_PATH):
        return dict(_DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            data = json.load(file)
            if not isinstance(data, dict):
                return dict(_DEFAULTS)
            return {**_DEFAULTS, **data}
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)


def save_config(updates: dict[str, object]) -> None:
    """Merge updates into existing config and write atomically. Thread-safe.

    The read and the write are held under one lock so concurrent callers
    merge onto each other instead of overwriting.
    """
    with _CONFIG_LOCK:
        current = load_config()
        current.update(updates)
        os.makedirs(CONFIG_DIR, exist_ok=True)
        save_json(CONFIG_PATH, current)


# ── Paths ────────────────────────────────────────────────────────────────────


class Paths:
    """All data paths resolved from the user-chosen root folder."""

    def __init__(self, data_root: str) -> None:
        self.root = data_root

        data_dir = os.path.join(data_root, ".data")
        self.tracks_path = os.path.join(data_dir, "tracks.json")
        self.albums_path = os.path.join(data_dir, "albums.json")
        self.preferences_path = os.path.join(data_dir, "preferences.json")
        self.logs_path = os.path.join(data_dir, "logs.jsonl")

        # Widget/CLI coordination — lives under config dir so it persists
        # independently of the user-chosen data root.
        self.ui_lock_path = os.path.join(CONFIG_DIR, ".ui.lock")
        self.widget_lock_path = os.path.join(CONFIG_DIR, ".widget.lock")
        self.widget_status_path = os.path.join(CONFIG_DIR, "widget_status.json")
        self.widget_cancel_path = os.path.join(CONFIG_DIR, ".widget_cancel")
        # Failures survive the detached worker that produced them, so the
        # widget can list and retry them instead of losing them on exit.
        self.widget_failures_path = os.path.join(CONFIG_DIR, "widget_failures.json")
        # Shared yt-dlp throttle state — every widget import is a new process
        # and the adaptive backoff must not restart from zero each time.
        self.youtube_state_path = os.path.join(CONFIG_DIR, ".youtube_throttle.json")

        self.playlists_dir = os.path.join(data_root, "playlists")
        self.tmp_dir = os.path.join(data_root, ".tmp")

        self.requests_path = os.path.join(data_root, "requetes.csv")
        self.shortcuts_dir = os.path.join(data_root, "raccourcis")
