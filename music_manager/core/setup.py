"""First-launch setup — folder picker, directory creation, demo files.

Creates the data folder structure inside the user-chosen data root:
- .data/          tracks.json, albums.json, preferences.json, logs.jsonl
- .tmp/           temporary files (downloads, covers)
- shortcuts/      web shortcuts (.webloc)
- playlists/      playlist CSVs
- requests.csv    demo CSV with example tracks
"""

import csv
import os
import subprocess

# ── Constants ────────────────────────────────────────────────────────────────

_WEBLOCS = {
    "Deezer.webloc": "https://www.deezer.com",
    "Exportify.webloc": "https://exportify.app",
    "YouTube.webloc": "https://www.youtube.com",
}

_WEBLOC_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>URL</key>
    <string>{url}</string>
</dict>
</plist>"""

_DEMO_TRACKS = [
    {
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "album": "A Night at the Opera",
        "isrc": "GBUM71029604",
    },
    {"title": "Imagine", "artist": "John Lennon", "album": "Imagine", "isrc": "USRC17000116"},
    {
        "title": "Hotel California",
        "artist": "Eagles",
        "album": "Hotel California",
        "isrc": "USEE10100142",
    },
]

_CSV_COLUMNS = ["title", "artist", "album", "isrc"]


# ── Entry point ──────────────────────────────────────────────────────────────


def choose_data_root() -> str | None:
    """Open macOS Finder folder picker. Returns chosen path or None if cancelled."""
    prompt = "Choisir le dossier racine Music Manager"
    script = (
        f'POSIX path of (choose folder with prompt "{prompt}" default location (path to desktop))'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().rstrip("/")


def create_data_folders(data_root: str) -> None:
    """Create all data directories, demo CSV, and web shortcuts."""
    for folder_name in (".data", "playlists", ".tmp", "raccourcis"):
        os.makedirs(os.path.join(data_root, folder_name), exist_ok=True)

    _write_demo_csv(data_root)
    _write_webloc_shortcuts(os.path.join(data_root, "raccourcis"))
    _init_empty_json(data_root)


# ── Private Functions ────────────────────────────────────────────────────────


def _write_demo_csv(data_root: str) -> None:
    """Create a demo CSV with example tracks (skip if already exists)."""
    csv_name = "requetes.csv"
    csv_path = os.path.join(data_root, csv_name)
    if os.path.exists(csv_path):
        return
    with open(csv_path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(_DEMO_TRACKS)


def _write_webloc_shortcuts(shortcuts_dir: str) -> None:
    """Create .webloc web shortcuts (skip existing ones)."""
    for filename, url in _WEBLOCS.items():
        webloc_path = os.path.join(shortcuts_dir, filename)
        if os.path.exists(webloc_path):
            continue
        with open(webloc_path, "w", encoding="utf-8") as file:
            file.write(_WEBLOC_TEMPLATE.format(url=url))


def _init_empty_json(data_root: str) -> None:
    """Create empty data files in .data/ if they don't exist."""
    data_dir = os.path.join(data_root, ".data")
    for filename in ("tracks.json", "albums.json", "preferences.json"):
        path = os.path.join(data_dir, filename)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as file:
                file.write("{}")
    logs_path = os.path.join(data_dir, "logs.jsonl")
    if not os.path.exists(logs_path):
        with open(logs_path, "w", encoding="utf-8") as file:
            pass
