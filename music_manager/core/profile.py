"""User music profile — local scoring over stored tracks.

Aggregates Apple Music usage signals (loved / play count / dates) into a
ranked profile of top tracks, artists and genres. Used to feed Last.fm
seeds for recommendations.

All input dates are ISO-like strings ("2024-06-15" or AppleScript date
descriptions); unparseable values contribute 0 to the recency bonus.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

# ── Constants ────────────────────────────────────────────────────────────────

LOVED_WEIGHT = 70
PLAY_COUNT_CAP = 30
PLAY_COUNT_MULTIPLIER = 2
RECENT_ADD_BONUS = 12
RECENT_ADD_DAYS = 90
RECENT_PLAY_BONUS = 10
RECENT_PLAY_DAYS = 30
VERY_RECENT_PLAY_BONUS = 10  # stacks with RECENT_PLAY_BONUS
VERY_RECENT_PLAY_DAYS = 7

TOP_TRACKS_LIMIT = 50
TOP_ARTISTS_LIMIT = 20
TOP_GENRES_LIMIT = 12


# ── Entry point ──────────────────────────────────────────────────────────────


@dataclass
class Profile:
    """Ranked summary of the user's library taste."""

    top_tracks: list[tuple[str, str, str]] = field(default_factory=list)
    """(isrc, title, artist) — sorted by score descending."""

    top_artists: list[tuple[str, int]] = field(default_factory=list)
    """(artist, aggregated_score) — sorted by score descending."""

    top_genres: list[tuple[str, int]] = field(default_factory=list)
    """(genre, occurrence_count) — sorted by count descending."""

    loved_isrcs: set[str] = field(default_factory=set)


def build_profile(
    tracks: dict[str, dict],
    *,
    mode: str = "general",
    playlist_apple_ids: set[str] | None = None,
) -> Profile:
    """Compute a Profile from a tracks store snapshot.

    The mode controls scope:
    - "general" / "library": consider the entire library (aliases).
    - "discovery": same scope as library — exploration bias lives in the
      ranking stage, not here.
    - "genre:<name>": restrict to tracks whose genre matches <name>
      (case-insensitive, exact match).
    - "playlist:<name>": restrict to tracks whose apple_id is in
      ``playlist_apple_ids`` (the caller resolves the playlist).
    - "mood:<tag>": no local scoping; caller drives via Last.fm tag.

    If ``playlist_apple_ids`` is provided, only those tracks contribute
    (independent of ``mode``). An empty set yields an empty Profile.

    Tracks without an ISRC are skipped: they cannot be deduplicated or
    used as Last.fm seeds reliably.
    """
    genre_filter = _parse_genre_filter(mode)
    use_playlist_filter = playlist_apple_ids is not None

    scored: list[tuple[float, str, str, str, str]] = []
    artist_scores: Counter[str] = Counter()
    genre_counts: Counter[str] = Counter()
    loved_isrcs: set[str] = set()

    now = datetime.now(UTC)

    for apple_id, entry in tracks.items():
        if use_playlist_filter and apple_id not in (playlist_apple_ids or set()):
            continue

        isrc = str(entry.get("isrc") or "").upper()
        if not isrc:
            continue

        genre = str(entry.get("genre") or "").strip()
        if genre_filter and genre.lower() != genre_filter:
            continue

        title = str(entry.get("title") or "").strip()
        artist = str(entry.get("artist") or "").strip()
        if not title or not artist:
            continue

        score = _score_entry(entry, now)
        if score <= 0:
            continue

        scored.append((score, isrc, title, artist, genre))
        artist_scores[artist] += int(score)
        if genre:
            genre_counts[genre] += 1
        if entry.get("loved"):
            loved_isrcs.add(isrc)

    scored.sort(key=lambda item: item[0], reverse=True)

    top_tracks = [
        (isrc, title, artist) for _score, isrc, title, artist, _g in scored[:TOP_TRACKS_LIMIT]
    ]
    top_artists = artist_scores.most_common(TOP_ARTISTS_LIMIT)
    top_genres = genre_counts.most_common(TOP_GENRES_LIMIT)

    return Profile(
        top_tracks=top_tracks,
        top_artists=top_artists,
        top_genres=top_genres,
        loved_isrcs=loved_isrcs,
    )


# ── Private Functions ────────────────────────────────────────────────────────


def _parse_genre_filter(mode: str) -> str | None:
    """Return the lowercased genre target for `genre:<name>`, else None."""
    if not mode.startswith("genre:"):
        return None
    target = mode.split(":", 1)[1].strip().lower()
    return target or None


def _score_entry(entry: dict, now: datetime) -> float:
    """Combine signals into a scalar score. 0 means the entry contributes nothing."""
    score = 0.0
    if entry.get("loved"):
        score += LOVED_WEIGHT

    play_count = int(entry.get("play_count") or 0)
    if play_count > 0:
        score += min(play_count, PLAY_COUNT_CAP) * PLAY_COUNT_MULTIPLIER

    added_age = _days_since(str(entry.get("added_date") or ""), now)
    if added_age is not None and added_age <= RECENT_ADD_DAYS:
        score += RECENT_ADD_BONUS

    played_age = _days_since(str(entry.get("last_played") or ""), now)
    if played_age is not None and played_age <= RECENT_PLAY_DAYS:
        score += RECENT_PLAY_BONUS
        if played_age <= VERY_RECENT_PLAY_DAYS:
            score += VERY_RECENT_PLAY_BONUS  # current-taste signal

    return score


def _days_since(date_str: str, now: datetime) -> int | None:
    """Parse a flexible date string and return days elapsed, or None if unparseable."""
    if not date_str:
        return None

    candidates = (
        date_str,
        date_str.replace(" +0000", "+0000"),
        date_str.split(" +", maxsplit=1)[0] if " +" in date_str else date_str,
        date_str.split("T", maxsplit=1)[0] if "T" in date_str else date_str,
    )
    parsed: datetime | None = None
    for candidate in candidates:
        parsed = _try_parse(candidate)
        if parsed is not None:
            break
    if parsed is None:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta = now - parsed
    return max(0, delta.days)


def _try_parse(value: str) -> datetime | None:
    """Try a few common formats. Returns None on failure."""
    formats = (
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
