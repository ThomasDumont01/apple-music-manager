"""Recommendations store — persistent memory of past picks and their outcomes.

Two-level memory keyed by ISRC (uppercase):

- ``active``: tracks currently sitting in their target playlist (one
  playlist per mode, all under the ``for me`` folder). Each entry carries
  the playlist name and ``last_seen_loved`` / ``last_seen_playcount``
  snapshots used to detect post-import deltas.
- ``outcomes``: tracks the user has acted on. Three states:
  - ``adopted_playlist`` — moved to another user playlist (strong positive)
  - ``kept_library``    — removed from the recommendation playlist but
                          still in the library (weak positive)
  - ``rejected``        — gone from the library entirely (negative)

Legacy ``blacklist`` schema is migrated to ``outcomes`` (state=rejected)
at load time.

Thread-safe (RLock); atomic save (.tmp + replace) via core/io.py.
"""

import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from music_manager.core.io import load_json, save_json

# ── Constants ────────────────────────────────────────────────────────────────

OutcomeState = Literal["adopted_playlist", "kept_library", "rejected"]
_VALID_STATES: frozenset[str] = frozenset(
    {"adopted_playlist", "kept_library", "rejected"}
)


# ── Entry point ──────────────────────────────────────────────────────────────


class RecommendationsStore:
    """In-memory store for recommendations.json."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()
        raw = load_json(path)
        self._active: dict[str, dict] = _coerce_section(raw.get("active"))
        self._outcomes: dict[str, dict] = _coerce_section(raw.get("outcomes"))
        self._dirty = False

        # Migration A: legacy ``blacklist`` section → ``outcomes`` (rejected).
        legacy = _coerce_section(raw.get("blacklist"))
        if legacy:
            for isrc, entry in legacy.items():
                if isrc in self._outcomes:
                    continue
                self._outcomes[isrc] = _migrate_legacy_blacklist(entry)
            self._dirty = True

        # Migration B: active entries pre-dating the per-playlist tracking get
        # an inferred ``playlist="library"`` (legacy mode "general" → library)
        # and default snapshots. Without this the new scan_outcomes / delta
        # detectors silently ignore them, locking them in active forever.
        for entry in self._active.values():
            if "playlist" not in entry:
                entry["playlist"] = "library"
                self._dirty = True
            if "last_seen_loved" not in entry:
                entry["last_seen_loved"] = bool(entry.get("loved", False))
                self._dirty = True
            if "last_seen_playcount" not in entry:
                try:
                    entry["last_seen_playcount"] = int(entry.get("play_count") or 0)
                except (TypeError, ValueError):
                    entry["last_seen_playcount"] = 0
                self._dirty = True

        stats_raw = raw.get("stats")
        self._stats: dict[str, object] = (
            dict(stats_raw) if isinstance(stats_raw, dict) else {}
        )

    # ── Queries ──────────────────────────────────────────────────────────────

    def is_active(self, isrc: str) -> bool:
        """Return True if the ISRC is currently sitting in a playlist."""
        if not isrc:
            return False
        with self._lock:
            return isrc.upper() in self._active

    def is_outcome(self, isrc: str) -> bool:
        """Return True if the ISRC has any recorded outcome."""
        if not isrc:
            return False
        with self._lock:
            return isrc.upper() in self._outcomes

    def is_adopted(self, isrc: str) -> bool:
        return self._is_state(isrc, "adopted_playlist")

    def is_kept(self, isrc: str) -> bool:
        return self._is_state(isrc, "kept_library")

    def is_rejected(self, isrc: str) -> bool:
        return self._is_state(isrc, "rejected")

    def is_blacklisted(self, isrc: str) -> bool:
        """Backward-compat alias: any outcome state blocks re-proposal."""
        return self.is_outcome(isrc)

    def all_active(self) -> dict[str, dict]:
        """Return a shallow copy of the active section."""
        with self._lock:
            return dict(self._active)

    def all_outcomes(self) -> dict[str, dict]:
        """Return a shallow copy of the outcomes section."""
        with self._lock:
            return dict(self._outcomes)

    def all_blacklist(self) -> dict[str, dict]:
        """Backward-compat alias for ``all_outcomes()``."""
        return self.all_outcomes()

    def stats(self) -> dict[str, object]:
        """Return a shallow copy of the stats section."""
        with self._lock:
            return dict(self._stats)

    # ── Mutations ────────────────────────────────────────────────────────────

    def add_active(self, entry: dict) -> dict:
        """Add an active recommendation. No-op if its ISRC is already known here.

        Required fields: ``isrc``, ``apple_id``, ``title``, ``artist``.
        Optional fields stored verbatim: ``source``, ``seed_isrc``, ``score``,
        ``mode``, ``playlist``, ``genre``. Snapshot fields ``last_seen_loved``
        and ``last_seen_playcount`` default to ``False`` / ``0``.

        Returns a status dict describing what happened so the caller can react:
        - ``{"added": True}``               — new entry inserted
        - ``{"added": False, "reason": "no_isrc"}``      — empty ISRC, skipped
        - ``{"added": False, "reason": "duplicate", "current_playlist": "..."}``
          — ISRC already active. ``current_playlist`` lets the caller decide
          whether to log a cross-playlist conflict.
        """
        isrc = str(entry.get("isrc") or "").upper()
        if not isrc:
            return {"added": False, "reason": "no_isrc"}
        with self._lock:
            existing = self._active.get(isrc)
            if existing is not None:
                return {
                    "added": False,
                    "reason": "duplicate",
                    "current_playlist": str(existing.get("playlist") or ""),
                }
            payload = dict(entry)
            payload["isrc"] = isrc
            payload.setdefault("added_at", _now_iso())
            payload.setdefault("last_seen_loved", False)
            payload.setdefault("last_seen_playcount", 0)
            self._active[isrc] = payload
            self._dirty = True
            return {"added": True}

    def record_outcome(
        self,
        isrc: str,
        *,
        state: str,
        from_playlist: str = "",
        to_playlists: Iterable[str] | None = None,
        title: str = "",
        artist: str = "",
        genre: str = "",
    ) -> None:
        """Record the outcome of a recommendation and remove it from active.

        Raises ``ValueError`` if ``state`` is not one of the three valid
        outcome states. Missing title/artist/genre are pulled from the
        active record (if any) for crash-resilient affinity signals.
        """
        if state not in _VALID_STATES:
            raise ValueError(f"invalid outcome state: {state!r}")
        if not isrc:
            return
        upper = isrc.upper()
        with self._lock:
            previous = self._active.pop(upper, None) or self._outcomes.get(upper, {})
            entry: dict = {
                "state": state,
                "outcome_at": _now_iso(),
                "from_playlist": from_playlist or previous.get("from_playlist", "")
                or previous.get("playlist", ""),
                "to_playlists": list(to_playlists) if to_playlists else [],
                "title": title or previous.get("title", ""),
                "artist": artist or previous.get("artist", ""),
                "genre": genre or previous.get("genre", ""),
                "seed_isrc": previous.get("seed_isrc", ""),
                "mode": previous.get("mode", ""),
            }
            self._outcomes[upper] = entry
            self._dirty = True

    def update_snapshot(self, isrc: str, *, loved: bool, playcount: int) -> None:
        """Refresh the ``last_seen_loved`` / ``last_seen_playcount`` of an active entry.

        No-op if the ISRC is not active (the entry may have moved to
        outcomes between two scans).
        """
        if not isrc:
            return
        upper = isrc.upper()
        with self._lock:
            entry = self._active.get(upper)
            if entry is None:
                return
            entry["last_seen_loved"] = bool(loved)
            entry["last_seen_playcount"] = int(playcount)
            self._dirty = True

    def blacklist(self, isrc: str, *, title: str = "", artist: str = "") -> None:
        """Backward-compat: blacklist == record_outcome(state='rejected')."""
        if not isrc:
            return
        self.record_outcome(isrc, state="rejected", title=title, artist=artist)

    def move_to_blacklist(self, isrcs: set[str]) -> int:
        """Blacklist every ISRC in the set. Returns the number actually moved.

        Backward-compat wrapper over ``record_outcome(state='rejected')``.
        ISRCs already present in outcomes that were not active are skipped
        (no double counting).
        """
        moved = 0
        with self._lock:
            for raw in isrcs:
                if not raw:
                    continue
                upper = raw.upper()
                was_active = upper in self._active
                was_outcome = upper in self._outcomes
                if not was_active and was_outcome:
                    continue
                self.record_outcome(upper, state="rejected")
                moved += 1
        return moved

    def seed_quality(self, *, min_samples: int = 3) -> dict[str, float]:
        """Return per-seed rejection ratio, across active + outcomes.

        For each ``seed_isrc``, the ratio is ``rejected / total``, where
        adoption and kept_library outcomes do **not** count as rejections
        (they are positive signals). Only seeds with at least
        ``min_samples`` total observations are reported.
        """
        with self._lock:
            counts: dict[str, list[int]] = {}  # seed_isrc → [positive, rejected]
            for entry in self._active.values():
                seed = str(entry.get("seed_isrc") or "").upper()
                if not seed:
                    continue
                counts.setdefault(seed, [0, 0])[0] += 1
            for entry in self._outcomes.values():
                seed = str(entry.get("seed_isrc") or "").upper()
                if not seed:
                    continue
                if entry.get("state") == "rejected":
                    counts.setdefault(seed, [0, 0])[1] += 1
                else:
                    counts.setdefault(seed, [0, 0])[0] += 1
        result: dict[str, float] = {}
        for seed, (positive_n, rejected_n) in counts.items():
            total = positive_n + rejected_n
            if total >= min_samples:
                result[seed] = rejected_n / total
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
                "outcomes": self._outcomes,
                "stats": self._stats,
            }
            save_json(self._path, payload)
            self._dirty = False

    # ── Private ──────────────────────────────────────────────────────────────

    def _is_state(self, isrc: str, target: str) -> bool:
        if not isrc:
            return False
        with self._lock:
            entry = self._outcomes.get(isrc.upper())
            return entry is not None and entry.get("state") == target


# ── Private Functions ────────────────────────────────────────────────────────


def _coerce_section(raw: object) -> dict[str, dict]:
    """Filter a section payload to keep only mapping values keyed by ISRC."""
    if not isinstance(raw, dict):
        return {}
    return {str(k).upper(): v for k, v in raw.items() if isinstance(v, dict)}


def _migrate_legacy_blacklist(entry: dict) -> dict:
    """Convert a legacy ``blacklist`` entry to the new ``outcomes`` shape."""
    return {
        "state": "rejected",
        "outcome_at": entry.get("removed_at", "") or _now_iso(),
        "from_playlist": entry.get("from_playlist", ""),
        "to_playlists": [],
        "title": entry.get("title", ""),
        "artist": entry.get("artist", ""),
        "genre": entry.get("genre", ""),
        "seed_isrc": entry.get("seed_isrc", ""),
        "mode": entry.get("mode", ""),
    }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
