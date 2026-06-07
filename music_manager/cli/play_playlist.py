"""`python -m music_manager play-playlist "Playlist Name"` — play a playlist.

Triggered by the Übersicht widget when the user clicks a playlist row on
the landing screen. Music is launched **fully hidden** via `open -gj`,
then the AppleScript just plays the playlist (no `activate`, no window
flash).
"""

import argparse
import json
import subprocess
import sys
import time

from music_manager.services.apple import _esc, run_applescript

# ── Constants ────────────────────────────────────────────────────────────────

_MUSIC_APP = "/System/Applications/Music.app"
_BOOT_DELAY = 5.0  # seconds — let Music load library + init audio engine
                    # when freshly launched (first click after reboot)

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="music_manager play-playlist")
    parser.add_argument("name", help="exact playlist name (case-sensitive)")
    parsed = parser.parse_args(args)

    raw_name = parsed.name.strip()
    if not raw_name:
        sys.stdout.write(json.dumps({"error": "empty_name"}))
        return 0

    # Lancement totalement silencieux :
    # - `open -gj <Music.app>` :
    #     -g = ne PAS amener Music au premier plan,
    #     -j = ne PAS afficher la fenêtre ("hide" l'app après le launch).
    #   Si Music tourne déjà, c'est un no-op instantané ; sinon il démarre
    #   en arrière-plan, fenêtre invisible.
    # - Délai 1s uniquement quand Music n'était pas encore lancé, pour lui
    #   laisser charger sa bibliothèque avant la commande AppleScript.
    # - Script AppleScript : juste `play` (pas d'`activate` qui ramènerait
    #   la fenêtre au premier plan).
    was_running = _music_was_running()
    subprocess.run(  # noqa: S603, S607
        ["open", "-gj", _MUSIC_APP],
        check=False,
        capture_output=True,
        timeout=5,
    )
    if not was_running:
        time.sleep(_BOOT_DELAY)

    escaped = _esc(raw_name)
    # Boucle de robustesse : Music peut prendre un peu de temps à
    # initialiser sa bibliothèque après un lancement à froid. On attend
    # que la playlist soit accessible (max ~3s) avant de jouer.
    script = (
        'tell application "Music"\n'
        "    set retries to 0\n"
        "    repeat while retries < 30\n"
        "        try\n"
        f'            set pl to user playlist "{escaped}"\n'
        "            exit repeat\n"
        "        on error\n"
        "            delay 0.1\n"
        "            set retries to retries + 1\n"
        "        end try\n"
        "    end repeat\n"
        "    play pl\n"
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
