"""Recommendation controllers and helpers shared between UI and pipeline.

Contains light-weight orchestration that doesn't belong in the pipeline
core (which stays focused on Last.fm/Deezer mechanics): playlist-seed
extraction, mode config dataclass, validation helpers.
"""

from dataclasses import dataclass, field

from music_manager.services import apple as apple_module
from music_manager.services.tracks import Tracks

# ── Constants ────────────────────────────────────────────────────────────────

_DEFAULT_PLAYLIST_SEED_LIMIT = 50


# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class RecommendationModeConfig:
    """Configuration for one generation run."""

    mode: str
    target_count: int = 20
    playlist_seed_name: str = ""
    seed_limit: int = _DEFAULT_PLAYLIST_SEED_LIMIT
    extra: dict[str, object] = field(default_factory=dict)


# ── Entry point ──────────────────────────────────────────────────────────────


def extract_playlist_seeds(
    playlist_name: str,
    tracks_store: Tracks,
    *,
    limit: int = _DEFAULT_PLAYLIST_SEED_LIMIT,
    apple_service=apple_module,
) -> list[tuple[str, str, str]]:
    """Extract ``(isrc, title, artist)`` seeds from an Apple Music user playlist.

    Steps:
    1. Resolve the playlist's apple_ids via AppleScript.
    2. Look up each in ``tracks_store`` to get ISRC + title + artist.
    3. Skip entries without an ISRC (no Last.fm seed possible).
    4. De-duplicate by ISRC, preserving the playlist order.
    5. Return at most ``limit`` seeds.

    Empty playlist or AppleScript failure → empty list (no crash).
    """
    if not playlist_name:
        return []
    try:
        apple_ids = apple_service.get_playlist_tracks(playlist_name)
    except Exception:  # noqa: BLE001
        return []
    if not apple_ids:
        return []

    seen: set[str] = set()
    seeds: list[tuple[str, str, str]] = []
    for apple_id in apple_ids:
        if len(seeds) >= limit:
            break
        if not apple_id:
            continue
        entry = tracks_store.get_by_apple_id(apple_id)
        if not entry:
            continue
        isrc = str(entry.get("isrc") or "").upper()
        if not isrc or isrc in seen:
            continue
        title = str(entry.get("title") or "").strip()
        artist = str(entry.get("artist") or "").strip()
        if not title or not artist:
            continue
        seen.add(isrc)
        seeds.append((isrc, title, artist))
    return seeds


def validate_playlist_exists(
    playlist_name: str,
    *,
    apple_service=apple_module,
    folder_name: str | None = None,
) -> bool:
    """Return True if a non-folder playlist exists.

    If ``folder_name`` is given, only playlists *outside* that folder
    are considered valid (used to filter out ``for me/*`` from the
    seed playlist selector).
    """
    if not playlist_name:
        return False
    try:
        playlists = apple_service.list_playlists(exclude_folder=folder_name)
    except Exception:  # noqa: BLE001
        return False
    return any(name == playlist_name for name, _count in playlists)
