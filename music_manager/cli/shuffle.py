"""`python -m music_manager shuffle` — shuffle-play the full library.

Triggered by the Übersicht widget when the user clicks the "Aléatoire"
row on the landing screen. Activates Music, switches shuffle mode on,
recentres the window (same heuristic as `play`), then starts playback
on the library.
"""

import json
import sys

from music_manager.services.apple import run_applescript

# ── Entry point ──────────────────────────────────────────────────────────────


def main(_args: list[str]) -> int:
    script = (
        'tell application "Music"\n'
        "    activate\n"
        "    try\n"
        "        set shuffle enabled to true\n"
        "    end try\n"
        "    try\n"
        "        tell front window\n"
        "            set {x1, y1, x2, y2} to bounds\n"
        "            set w to x2 - x1\n"
        "            set h to y2 - y1\n"
        "            if w < 1200 then set w to 1200\n"
        "            if h < 800 then set h to 800\n"
        "            set newX to x1\n"
        "            set newY to y1\n"
        "            try\n"
        "                tell application \"Finder\" to "
        "set screenBounds to bounds of window of desktop\n"
        "                set newX to (((item 3 of screenBounds) - w) / 2) as integer\n"
        "                set newY to (((item 4 of screenBounds) - h) / 2) as integer\n"
        "            end try\n"
        "            set bounds to {newX, newY, newX + w, newY + h}\n"
        "        end tell\n"
        "    end try\n"
        "    try\n"
        "        stop\n"
        "    end try\n"
        "    delay 0.4\n"
        "    play library playlist 1\n"
        "end tell"
    )
    result = run_applescript(script)
    if result is None:
        sys.stdout.write(json.dumps({"error": "applescript_failed"}))
        return 0
    sys.stdout.write(json.dumps({"status": "ok"}))
    return 0
