"""Recommendations store — persistent memory of past Last.fm picks.

Two-level memory keyed by ISRC (uppercase):
- ``active``: tracks currently sitting in the Apple Music "Recommandations"
  playlist. Used for "do not repropose what we already proposed".
- ``blacklist``: tracks the user has explicitly removed (or that never
  reached the playlist for any reason). These must never be proposed
  again — that's the entire point of remembering deletions.

Thread-safe (RLock); atomic save (.tmp + replace) via core/io.py.
"""

import threading
from datetime import UTC, datetime

from music_manager.core.io import load_json, save_json

# ── Constants ────────────────────────────────────────────────────────────────

_SECTIONS = ("active", "blacklist", "stats")


# ── Entry point ──────────────────────────────────────────────────────────────


class RecommendationsStore:
    """In-memory store for recommendations.json."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()
        raw = load_json(path)
        self._active: dict[str, dict] = _coerce_section(raw.get("active"))
        self._blacklist: dict[str, dict] = _coerce_section(raw.get("blacklist"))
        stats_raw = raw.get("stats")
        self._stats: dict[str, object] = (
            dict(stats_raw) if isinstance(stats_raw, dict) else {}
        )
        self._dirty = False

    # ── Queries ──────────────────────────────────────────────────────────────

    def is_active(self, isrc: str) -> bool:
        """Return True if the ISRC is currently sitting in the playlist."""
        if not isrc:
            return False
        with self._lock:
            return isrc.upper() in self._active

    def is_blacklisted(self, isrc: str) -> bool:
        """Return True if the user has explicitly removed this ISRC."""
        if not isrc:
            return False
        with self._lock:
            return isrc.upper() in self._blacklist

    def all_active(self) -> dict[str, dict]:
        """Return a shallow copy of the active section."""
        with self._lock:
            return dict(self._active)

    def all_blacklist(self) -> dict[str, dict]:
        """Return a shallow copy of the blacklist section."""
        with self._lock:
            return dict(self._blacklist)

    def stats(self) -> dict[str, object]:
        """Return a shallow copy of the stats section."""
        with self._lock:
            return dict(self._stats)

    # ── Mutations ────────────────────────────────────────────────────────────

    def add_active(self, entry: dict) -> None:
        """Add an active recommendation. No-op if its ISRC is already known here.

        Required fields: ``isrc``, ``apple_id``, ``title``, ``artist``.
        Optional: ``source``, ``seed_isrc``, ``score``, ``mode``.
        """
        isrc = str(entry.get("isrc") or "").upper()
        if not isrc:
            return
        with self._lock:
            if isrc in self._active:
                return
            payload = dict(entry)
            payload["isrc"] = isrc
            payload.setdefault("added_at", _now_iso())
            self._active[isrc] = payload
            self._dirty = True

    def blacklist(self, isrc: str, *, title: str = "", artist: str = "") -> None:
        """Move (or insert) an ISRC into the blacklist."""
        if not isrc:
            return
        upper = isrc.upper()
        with self._lock:
            previous = self._active.pop(upper, None)
            self._blacklist[upper] = {
                "removed_at": _now_iso(),
                "title": title or (previous or {}).get("title", ""),
                "artist": artist or (previous or {}).get("artist", ""),
                "seed_isrc": (previous or {}).get("seed_isrc", ""),
            }
            self._dirty = True

    def move_to_blacklist(self, isrcs: set[str]) -> int:
        """Blacklist every ISRC in the set. Returns the number actually moved.

        ISRCs that are not in ``active`` are still recorded in the blacklist
        (the caller knows something about them — typically that they were
        deleted by the user).
        """
        moved = 0
        with self._lock:
            for raw in isrcs:
                if not raw:
                    continue
                upper = raw.upper()
                previous = self._active.pop(upper, None)
                if upper in self._blacklist and not previous:
                    continue
                self._blacklist[upper] = {
                    "removed_at": _now_iso(),
                    "title": (previous or {}).get("title", ""),
                    "artist": (previous or {}).get("artist", ""),
                    "seed_isrc": (previous or {}).get("seed_isrc", ""),
                }
                moved += 1
                self._dirty = True
        return moved

    def seed_quality(self, *, min_samples: int = 3) -> dict[str, float]:
        """Return per-seed blacklist ratio, computed across history.

        For each ``seed_isrc`` known in active or blacklist sections, the
        ratio is ``blacklisted / (active + blacklisted)``. Only seeds with
        at least ``min_samples`` total observations are reported (a single
        bad pick doesn't condemn a seed).

        The recommendations pipeline uses this to skip seeds whose ratio
        is above a threshold (negative reinforcement loop).
        """
        with self._lock:
            counts: dict[str, list[int]] = {}  # seed_isrc → [active, blacklisted]
            for entry in self._active.values():
                seed = str(entry.get("seed_isrc") or "").upper()
                if not seed:
                    continue
                counts.setdefault(seed, [0, 0])[0] += 1
            for entry in self._blacklist.values():
                seed = str(entry.get("seed_isrc") or "").upper()
                if not seed:
                    continue
                counts.setdefault(seed, [0, 0])[1] += 1

        result: dict[str, float] = {}
        for seed, (active_n, blacklisted_n) in counts.items():
            total = active_n + blacklisted_n
            if total >= min_samples:
                result[seed] = blacklisted_n / total
        return result

    def record_generation(self) -> None:
        """Bump the ``generations`` counter and update ``last_run``."""
        with self._lock:
            raw = self._stats.get("generations") or 0
            current = int(raw) if isinstance(raw, int | str) else 0
            self._stats["generations"] = current + 1
            self._stats["last_run"] = _now_iso()
            self._dirty = True

    # ── Persistence ──────────────────────────────────────────────────────────

    def mark_dirty(self) -> None:
        self._dirty = True

    def save(self) -> None:
        """Write to disk if modified since the last save."""
        with self._lock:
            if not self._dirty:
                return
            payload = {
                "active": self._active,
                "blacklist": self._blacklist,
                "stats": self._stats,
            }
            save_json(self._path, payload)
            self._dirty = False


# ── Private Functions ────────────────────────────────────────────────────────


def _coerce_section(raw: object) -> dict[str, dict]:
    """Filter a section payload to keep only mapping values keyed by ISRC."""
    if not isinstance(raw, dict):
        return {}
    return {str(k).upper(): v for k, v in raw.items() if isinstance(v, dict)}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
