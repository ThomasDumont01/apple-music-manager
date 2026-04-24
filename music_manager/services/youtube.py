"""YouTube audio search and download via yt-dlp.

Searches by ISRC (returns Topic channels = official label audio).
Downloads best audio as M4A.
"""

import glob
import json
import os
import subprocess

from music_manager.core.logger import log_event

# ── Constants ────────────────────────────────────────────────────────────────

_SEARCH_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 120


# ── Entry point ──────────────────────────────────────────────────────────────


def search_by_isrc(isrc: str) -> list[dict]:
    """Search YouTube by ISRC. Returns candidates sorted by Topic channel first.

    Each candidate: {id, title, url, duration, channel}.
    """
    if not isrc:
        return []

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--dump-json",
                "--skip-download",
                "--no-playlist",
                "--quiet",
                f"ytsearch5:{isrc}",
            ],
            capture_output=True,
            text=True,
            timeout=_SEARCH_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return []

    candidates = []
    for line in result.stdout.strip().splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidates.append(
            {
                "id": data.get("id", ""),
                "title": data.get("title", ""),
                "url": data.get("webpage_url", ""),
                "duration": data.get("duration") or 0,
                "channel": data.get("channel", ""),
            }
        )

    candidates.sort(key=lambda candidate: 0 if "topic" in candidate["channel"].lower() else 1)

    log_event("youtube_search", isrc=isrc, results=len(candidates))
    return candidates


def download_track(url: str, output_dir: str) -> tuple[str, int | None]:
    """Download a YouTube audio as M4A. Returns (filepath, duration).

    Raises RuntimeError on failure.
    """
    os.makedirs(output_dir, exist_ok=True)
    output_template = os.path.join(output_dir, "%(id)s.%(ext)s")

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--format",
                "bestaudio",
                "--extract-audio",
                "--audio-format",
                "m4a",
                "--audio-quality",
                "0",
                "--output",
                output_template,
                "--no-playlist",
                "--quiet",
                "--print",
                "after_move:filepath",
                "--print",
                "after_move:duration",
                "--",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=_DOWNLOAD_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _cleanup_partial(output_dir)
        raise RuntimeError(f"yt-dlp timeout after {_DOWNLOAD_TIMEOUT}s") from None

    if result.returncode != 0:
        _cleanup_partial(output_dir)
        raise RuntimeError(f"yt-dlp error: {result.stderr.strip()}") from None

    filepath, duration = _parse_output(result.stdout)
    if filepath and os.path.exists(filepath):
        return filepath, duration

    filepath = _find_latest_m4a(output_dir)
    if filepath:
        return filepath, duration

    raise RuntimeError("Audio file not found after download")


# ── Private Functions ────────────────────────────────────────────────────────


def _parse_output(stdout: str) -> tuple[str, int | None]:
    """Parse yt-dlp output for filepath and duration."""
    lines = stdout.strip().splitlines()
    filepath = lines[0] if lines else ""
    duration = None
    if len(lines) >= 2:
        try:
            duration = int(lines[1])
        except ValueError:
            pass
    return filepath, duration


def _cleanup_partial(output_dir: str) -> None:
    """Remove .part files left by incomplete downloads."""
    for partial in glob.glob(os.path.join(output_dir, "*.part")):
        try:
            os.remove(partial)
        except OSError:
            pass


def _find_latest_m4a(output_dir: str) -> str:
    """Find the most recent M4A file in directory."""
    files = sorted(
        glob.glob(os.path.join(output_dir, "*.m4a")),
        key=os.path.getmtime,
        reverse=True,
    )
    return files[0] if files else ""
