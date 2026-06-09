"""`python -m music_manager spotify-auth-status` — JSON of token state.

Output schema (stable, widget-consumed)::

    {"authenticated": true|false, "expires_in": <seconds>,
     "client_id_set": true|false}

Reads config.json only — no Spotify API call. Always exits 0.
"""

import json
import sys
import time

from music_manager.services.spotify import get_client_id, load_tokens

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    tokens = load_tokens()
    authenticated = bool(tokens.get("refresh_token"))
    expiry = float(tokens.get("expiry") or 0.0)
    remaining = max(0, int(expiry - time.time())) if authenticated else 0
    sys.stdout.write(
        json.dumps(
            {
                "authenticated": authenticated,
                "expires_in": remaining,
                "client_id_set": bool(get_client_id()),
            }
        )
    )
    return 0
