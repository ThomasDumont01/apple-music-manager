"""Find and manage duplicate tracks in library."""

from music_manager.core.io import load_json, save_json
from music_manager.core.normalize import first_artist, normalize
from music_manager.services.apple import delete_tracks
from music_manager.services.tracks import Tracks

# ── Entry point ──────────────────────────────────────────────────────────────


def find_duplicates(tracks_store: Tracks) -> list[list[dict]]:
    """Find duplicate groups among identified tracks.

    Only considers tracks with deezer_id and status != 'failed'.

    Three-level grouping:
    1. Same deezer_id → definite duplicate
    2. Same ISRC → merge groups (same recording)
    3. Same normalize(title) + normalize(first_artist) → merge if ISRCs don't conflict
    """
    tracks = tracks_store.all()
    groups: dict[str, list[dict]] = {}

    for apple_id, entry in tracks.items():
        if not entry.get("deezer_id") or entry.get("status") == "failed":
            continue
        key = f"dz:{entry['deezer_id']}"
        groups.setdefault(key, []).append({**entry, "_apple_id": apple_id})

    _merge_by_isrc(groups)
    _merge_by_title_artist(groups)

    return [group for group in groups.values() if len(group) >= 2]


def best_version(group: list[dict]) -> int:
    """Return index of the best version in a duplicate group.

    Priority: has deezer_id > has ISRC > longest duration.
    """

    def score(entry: dict) -> tuple:
        return (
            bool(entry.get("deezer_id")),
            bool(entry.get("isrc")),
            entry.get("duration") or 0,
        )

    return max(range(len(group)), key=lambda index: score(group[index]))


def remove_duplicates(apple_ids: list[str], tracks_store: Tracks) -> int:
    """Remove duplicate tracks from Apple Music and store.

    Returns count of tracks deleted from Apple Music.
    """
    count = delete_tracks(apple_ids)
    for apple_id in apple_ids:
        tracks_store.remove(apple_id)
    return count


def group_key(group: list[dict]) -> str:
    """Build canonical key for a duplicate group (sorted unique deezer_ids)."""
    dz_ids = sorted({str(e.get("deezer_id", 0)) for e in group})
    return ",".join(dz_ids)


def ignore_group(group: list[dict], preferences_path: str) -> None:
    """Mark a duplicate group as permanently ignored."""
    prefs = load_json(preferences_path)
    raw = prefs.get("ignored_duplicates", [])
    ignored = set(raw) if isinstance(raw, list) else set()
    ignored.add(group_key(group))
    prefs["ignored_duplicates"] = sorted(ignored)
    save_json(preferences_path, prefs)


def load_ignored(preferences_path: str) -> set[str]:
    """Load ignored duplicate group keys from preferences."""
    prefs = load_json(preferences_path)
    raw = prefs.get("ignored_duplicates", [])
    return set(raw) if isinstance(raw, list) else set()


# ── Private Functions ────────────────────────────────────────────────────────


def _merge_by_isrc(groups: dict[str, list[dict]]) -> None:
    """Merge groups sharing an ISRC (case-insensitive)."""
    isrc_to_key: dict[str, str] = {}
    merges: dict[str, str] = {}
    for key, entries in groups.items():
        for entry in entries:
            isrc = (entry.get("isrc") or "").upper()
            if not isrc:
                continue
            if isrc in isrc_to_key and isrc_to_key[isrc] != key:
                merges[key] = isrc_to_key[isrc]
            else:
                isrc_to_key[isrc] = key

    for source, target in merges.items():
        if source in groups and target in groups:
            groups[target].extend(groups.pop(source))


def _merge_by_title_artist(groups: dict[str, list[dict]]) -> None:
    """Merge groups with same normalized title + first_artist.

    Checks ALL entries in each group (not just the first) to handle
    groups formed by ISRC merge with mixed titles.

    Skips merge if both groups have entries with conflicting ISRCs
    (different recordings of the same song).
    """
    ta_to_key: dict[str, str] = {}
    merges: list[tuple[str, str]] = []

    for key, entries in groups.items():
        matched_existing = ""
        for entry in entries:
            title_norm = normalize(entry.get("title", ""))
            artist_norm = normalize(first_artist(entry.get("artist", "")))
            ta_key = f"{title_norm}||{artist_norm}"

            if ta_key in ta_to_key and ta_to_key[ta_key] != key:
                matched_existing = ta_to_key[ta_key]
                break
            ta_to_key.setdefault(ta_key, key)

        if matched_existing:
            if not _isrc_conflict(groups.get(matched_existing, []), entries):
                merges.append((key, matched_existing))

    for source, target in merges:
        if source in groups and target in groups:
            groups[target].extend(groups.pop(source))


def _isrc_conflict(group_a: list[dict], group_b: list[dict]) -> bool:
    """Return True if both groups have ISRCs and none overlap."""
    isrcs_a = {(e.get("isrc") or "").upper() for e in group_a} - {""}
    isrcs_b = {(e.get("isrc") or "").upper() for e in group_b} - {""}
    return bool(isrcs_a and isrcs_b and not (isrcs_a & isrcs_b))
