"""`python -m music_manager import-status` — emit widget_status.json on stdout.

If the file is absent, returns ``{"status": "idle"}`` so the widget can
distinguish "nothing ran" from "run in progress" without special casing.
"""

import json
import sys

from music_manager.core.config import Paths, load_config

# ── Entry point ──────────────────────────────────────────────────────────────


def main(_args: list[str]) -> int:
    """Print the current widget status as JSON."""
    config = load_config()
    data_root = str(config.get("data_root") or "")
    paths = Paths(data_root)

    payload = read_status(paths.widget_status_path)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


# ── Public helpers ───────────────────────────────────────────────────────────


def read_status(path: str) -> dict:
    """Return the parsed status file, or the idle sentinel if absent/corrupt."""
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"status": "idle"}
