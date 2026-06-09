"""`python -m music_manager spotify-logout` — clear Spotify tokens."""

import json
import sys

from music_manager.services.spotify import clear_tokens

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    clear_tokens()
    sys.stdout.write(json.dumps({"status": "ok"}))
    return 0
