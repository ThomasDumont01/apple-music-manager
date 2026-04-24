"""Data models — storage shapes used throughout the project.

ISRC (International Standard Recording Code) is used as the universal
identifier. It's the only metadata field guaranteed to stay the same
across platforms (Deezer, Apple Music). Unlike the track
title or artist name — which can vary slightly between platforms
("Bohemian Rhapsody" vs "Bohemian Rhapsody - Remastered 2011") — the
ISRC is stable and unique per recording.

Three models:
- Track: resolved track (metadata + cover), keyed by ISRC in tracks.json
- Album: resolved album (Deezer data + cover), keyed by Deezer album ID
- LibraryEntry: track already in the Apple Music library, keyed by apple_id
"""

from dataclasses import asdict, dataclass, field, fields
from typing import Self

# ── Base ─────────────────────────────────────────────────────────────────────


@dataclass
class _BaseEntry:
    """Base class with dict ↔ object conversion for JSON persistence."""

    def to_dict(self) -> dict[str, object]:
        """Convert the dataclass instance to a dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Self:
        """Create an instance of the dataclass from a dictionary."""
        # Backwards compat: total_disks was renamed to total_discs
        if "total_disks" in data and "total_discs" not in data:
            data = {**data, "total_discs": data["total_disks"]}
        known_fields = {fld.name for fld in fields(cls)}
        filtered = {key: value for key, value in data.items() if key in known_fields}
        return cls(**filtered)


# ── Track (source: Deezer → enriched with iTunes cover) ─────────────────────


@dataclass
class Track(_BaseEntry):
    """Resolved track — ready to import.

    ISRC is the universal identifier. All operations (import, dedup,
    fix-metadata, YouTube) go through the ISRC.
    """

    # Identity (ISRC = key in tracks.json)
    isrc: str
    title: str
    artist: str
    album: str

    # Album
    album_id: int = 0  # Deezer album ID → link to albums.json
    genre: str = ""
    release_date: str = ""
    track_number: int | None = None
    total_tracks: int | None = None
    disk_number: int = 0
    total_discs: int = 0
    album_artist: str = ""

    # Technical
    duration: int = 0
    explicit: bool = False
    cover_url: str = ""  # iTunes 3000x3000 or Deezer fallback
    preview_url: str = ""  # Deezer 30s preview
    deezer_id: int = 0

    # Apple Music
    apple_id: str = ""

    # Import state
    origin: str = ""  # "imported" or "baseline"
    status: str | None = None  # None / "done" / "failed"
    fail_reason: str = ""
    imported_at: str = ""

    # CSV traceability
    csv_title: str = ""
    csv_artist: str = ""
    csv_album: str = ""


# ── Album (Deezer cache + iTunes cover) ──────────────────────────────────────


@dataclass
class Album(_BaseEntry):
    """Resolved album — cache rebuildable from Deezer + iTunes."""

    # Identity (Deezer album ID = key in albums.json)
    id: int
    title: str
    artist: str

    # Metadata
    album_artist: str = ""
    year: str = ""
    genre: str = ""
    release_date: str = ""
    total_tracks: int = 0
    total_discs: int = 0

    # Cover (iTunes 3000×3000 if available, Deezer fallback)
    cover_url: str = ""

    # Cache
    fetched_at: str = ""


# ── LibraryEntry (existing in Apple Music) ──────────────────────────────────


@dataclass
class LibraryEntry(_BaseEntry):
    """Track existing in the Apple Music library.

    Read via AppleScript. apple_id (persistent ID) is the key for all
    Apple Music operations (update, delete, playlist).
    """

    # Identity
    apple_id: str
    title: str
    artist: str
    album: str

    # Metadata
    year: str = ""
    genre: str = ""
    track_number: int | None = None
    total_tracks: int | None = None
    disk_number: int = 0
    album_artist: str = ""

    # Technical
    duration: float = 0.0
    explicit: bool = False
    has_artwork: bool = False
    isrc: str = ""
    file_path: str = ""


# ── PendingTrack (blocked import, awaiting review) ─────────────────────────


@dataclass
class PendingTrack:
    """Track that could not be imported automatically.

    Created by import_resolved_track() when a step fails or needs
    user decision. Lives in memory only (not persisted).
    """

    reason: str  # "not_found", "mismatch", "ambiguous", "youtube_failed", "duration_suspect"

    # Original CSV request
    csv_title: str = ""
    csv_artist: str = ""
    csv_album: str = ""

    # Resolved track (None if not_found)
    track: Track | None = None

    # Mismatch detail
    album_mismatch: bool = False

    # Deezer candidates (for ambiguous)
    candidates: list[dict] = field(default_factory=list)

    # YouTube data (for duration_suspect)
    dl_path: str = ""
    actual_duration: int = 0
    youtube_candidates: list[dict] = field(default_factory=list)
