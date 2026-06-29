"""`python -m music_manager play APPLE_ID` — focus Apple Music + play a track.

Invoked by the Übersicht widget when the user clicks the ``♪`` icon next
to a result already present in the library. The widget passes the
``apple_id`` (16-char hex persistent ID) it received from the ``search``
output.

Output is intentionally minimal: ``{"status": "ok"}`` on success,
``{"error": "..."}`` otherwise. Exit code 0 even on AppleScript failure
so the widget doesn't show a generic crash — the JSON ``error`` field
already conveys the issue.
"""

import argparse
import json
import re
import subprocess
import sys
import time

from music_manager.services.apple import run_applescript

# ── Constants ────────────────────────────────────────────────────────────────

# Apple Music persistent ID = 16 uppercase hex chars (cf. apple.py:_scan).
_APPLE_ID_RE = re.compile(r"^[A-F0-9]{16}$")
_MUSIC_APP = "/System/Applications/Music.app"
_BOOT_DELAY = 5.0  # seconds — let Music load library + init audio engine
# when freshly launched (first click after reboot)


# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager play")
    parser.add_argument(
        "apple_id",
        help="16-char hex Apple Music persistent ID (from `search` output)",
    )
    parsed = parser.parse_args(args)

    apple_id = parsed.apple_id.strip().upper()
    if not _APPLE_ID_RE.match(apple_id):
        sys.stdout.write(json.dumps({"error": "invalid_apple_id"}))
        return 0

    # Lancement totalement silencieux : `open -gj` démarre Music caché,
    # puis on joue la track via AppleScript SANS `activate` (pas de
    # fenêtre qui apparaît). Voir play_playlist.py pour les détails.
    was_running = _music_was_running()
    subprocess.run(  # noqa: S603, S607
        ["open", "-gj", _MUSIC_APP],
        check=False,
        capture_output=True,
        timeout=5,
    )
    if not was_running:
        time.sleep(_BOOT_DELAY)

    # Boucle de robustesse : Music peut prendre un peu de temps à
    # initialiser sa bibliothèque après un lancement à froid. On attend
    # que la track soit accessible (max ~3s) avant de jouer.
    script = (
        'tell application "Music"\n'
        "    set retries to 0\n"
        "    repeat while retries < 30\n"
        "        try\n"
        "            set t to first track of library playlist 1"
        f' whose persistent ID is "{apple_id}"\n'
        "            exit repeat\n"
        "        on error\n"
        "            delay 0.1\n"
        "            set retries to retries + 1\n"
        "        end try\n"
        "    end repeat\n"
        "    play t\n"
        "end tell"
    )
    result = run_applescript(script)
    if result is None:
        sys.stdout.write(json.dumps({"error": "applescript_failed"}))
        return 0

    sys.stdout.write(json.dumps({"status": "ok"}))
    return 0


def _music_was_running() -> bool:
    """Return True if a Music process is already running on this system."""
    try:
        result = subprocess.run(  # noqa: S603, S607
            ["pgrep", "-x", "Music"],
            check=False,
            capture_output=True,
            timeout=2,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False
