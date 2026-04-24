"""Dedup check — verify if a track already exists before importing.

Centralized logic used by §6 Import and §8 Complete Albums.
"""

from music_manager.core.normalize import first_artist, normalize, prepare_title
from music_manager.services.tracks import Tracks

# ── Entry point ──────────────────────────────────────────────────────────────


def is_duplicate(
    isrc: str,
    title: str,
    artist: str,
    tracks_store: Tracks,
) -> bool:
    """Check if a track already exists among IDENTIFIED tracks.

    Only checks tracks with deezer_id (identified). Unidentified baseline
    tracks are ignored to avoid false positives from bad metadata.

    Returns True for any identified entry except status "failed".

    Three-level check:
    1. ISRC exact match (fastest, most reliable)
    2. Strict normalize(title+artist) — preserves edition markers
    3. Soft fallback: prepare_title (strips parens) + first_artist
    """

    isrc = (isrc or "").upper()

    def _is_valid(entry: dict) -> bool:
        return bool(entry.get("deezer_id")) and entry.get("status") != "failed"

    # By ISRC (exact)
    if isrc:
        entry = tracks_store.get_by_isrc(isrc)
        if entry and _is_valid(entry):
            return True

    # Precompute normalized forms
    norm_title = normalize(title)
    norm_artist = normalize(artist)

    # Level 2: O(1) index lookup by normalized title+artist
    # Exact title+artist match is strong enough — ISRC can differ across
    # editions/territories (Spotify vs Deezer vs Apple) for the same song.
    entry = tracks_store.get_by_title_artist(norm_title, norm_artist)
    if entry and _is_valid(entry):
        return True

    # Level 2b + Level 3: need linear scan for soft matching
    prep_title = prepare_title(title)
    first_norm_artist = normalize(first_artist(artist))

    for entry in tracks_store.all().values():
        if not _is_valid(entry):
            continue

        entry_isrc = (entry.get("isrc", "") or "").upper()
        isrc_conflict = bool(isrc and entry_isrc and isrc != entry_isrc)

        # Level 2b: strict normalize against stored CSV title
        # Exact match is reliable even with ISRC conflict (same song, different edition)
        csv_t = entry.get("csv_title") or ""
        if (
            csv_t
            and normalize(csv_t) == norm_title
            and normalize(entry.get("csv_artist") or "") == norm_artist
        ):
            return True

        # Level 3: soft fallback — strips parens + primary artist only
        # Skip if ISRC conflict (fuzzy match too weak for conflicting ISRCs)
        if isrc_conflict:
            continue

        if (
            prepare_title(entry.get("title") or "") == prep_title
            and normalize(first_artist(entry.get("artist") or "")) == first_norm_artist
        ):
            return True

    return False
