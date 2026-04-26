"""Application configuration — load/save from ~/.config/music_manager/config.json.

Stores user preferences that persist across sessions:
- data_root: path to the user's data folder
- setup_done: whether the first-launch scan has been completed
"""

import json
import os

# ── Constants ────────────────────────────────────────────────────────────────

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "music_manager")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

_DEFAULTS = {
    "data_root": "",
    "setup_done": False,
    "last_log_upload": "",
    "install_id": "",
    "telemetry_consent": True,
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
    """Merge updates into existing config and write atomically (tmp + replace)."""
    current = load_config()
    current.update(updates)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(current, file, ensure_ascii=False, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


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

        self.playlists_dir = os.path.join(data_root, "playlists")
        self.tmp_dir = os.path.join(data_root, ".tmp")

        self.requests_path = os.path.join(data_root, "requetes.csv")
        self.shortcuts_dir = os.path.join(data_root, "raccourcis")
