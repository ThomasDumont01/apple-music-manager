"""Export playlists to CSV (§10)."""

from music_manager.core.io import save_csv

# ── Entry point ──────────────────────────────────────────────────────────────


def export_playlist(tracks: list[dict], filepath: str) -> int:
    """Export a list of tracks to CSV. Returns count exported."""
    rows = []
    for track in tracks:
        rows.append(
            {
                "title": track.get("title", ""),
                "artist": track.get("artist", ""),
                "album": track.get("album", ""),
                "genre": track.get("genre", ""),
                "year": track.get("year", ""),
                "duration": track.get("duration", ""),
                "track_number": track.get("track_number", ""),
                "disk_number": track.get("disk_number", ""),
                "album_artist": track.get("album_artist", ""),
                "isrc": track.get("isrc", ""),
            }
        )
    save_csv(filepath, rows)
    return len(rows)
