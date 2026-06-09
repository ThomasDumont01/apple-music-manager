"""`python -m music_manager spotify-set-client-id <CLIENT_ID>`.

Persists a Spotify Web API Client ID (32-char hex string, public per PKCE
flow) into ``config.json``. Used by the Übersicht widget so the user can
configure Spotify without editing the file by hand.

JSON stdout: ``{"status": "ok"}`` on success, ``{"error": "..."}`` otherwise.
"""

import argparse
import json
import re
import sys

from music_manager.core.config import save_config

# ── Constants ────────────────────────────────────────────────────────────────

_CLIENT_ID_RE = re.compile(r"^[a-fA-F0-9]{32}$")


# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager spotify-set-client-id")
    parser.add_argument("client_id", help="Spotify Web API Client ID (32 hex chars)")
    parsed = parser.parse_args(args)

    client_id = parsed.client_id.strip()
    if not _CLIENT_ID_RE.match(client_id):
        sys.stdout.write(json.dumps({"error": "invalid_client_id_format"}))
        return 1
    save_config({"spotify_client_id": client_id.lower()})
    sys.stdout.write(json.dumps({"status": "ok"}))
    return 0
