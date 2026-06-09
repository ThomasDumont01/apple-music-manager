"""Event log for the recommendations adaptive learning system.

Append-only JSONL: each line is a self-contained event. Crash-safe
(an interrupted append leaves at most one truncated line, skipped on
read). Used to compute artist/genre affinity from past outcomes over
a sliding window.

Event types currently consumed by affinity:
- ``recommend_adopted_playlist``  weight +1.0
- ``recommend_kept_library``      weight +0.5
- ``recommend_rejected``          weight -1.0
- ``loved_delta`` (``to_loved=True``)        weight +0.7
- ``playcount_delta`` (``delta > 0``)        weight +0.3

Other event types (``recommend_imported``, ``generation_run``) are
logged for audit but do not contribute to affinity scoring.
"""

import json
import os
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_AFFINITY_WINDOW_DAYS = 180
DEFAULT_MIN_SAMPLES = 3

_EVENT_WEIGHTS: dict[str, float] = {
    "recommend_adopted_playlist": 1.0,
    "recommend_kept_library": 0.5,
    "recommend_rejected": -1.0,
    "loved_delta": 0.7,
    "playcount_delta": 0.3,
}


# ── Entry point ──────────────────────────────────────────────────────────────


class SignalsLog:
    """Append-only event log on disk (JSONL).

    Writes are guarded by an in-process RLock. Reads tolerate corrupt
    lines (skipped silently) and non-mapping payloads.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()

    # ── Mutations ────────────────────────────────────────────────────────────

    def log(self, event_type: str, **payload: Any) -> None:
        """Append one event line. Auto-stamps ``ts`` (ISO UTC) and ``type``.

        If a previous write was interrupted and the file does not end
        with a newline, prepend one so the truncated tail stays isolated
        (and gets skipped by the JSON parser on read).
        """
        if not event_type:
            return
        record = {"ts": _now_iso(), "type": event_type, **payload}
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            need_leading_newline = False
            if os.path.isfile(self._path) and os.path.getsize(self._path) > 0:
                with open(self._path, "rb") as file:
                    file.seek(-1, os.SEEK_END)
                    if file.read(1) != b"\n":
                        need_leading_newline = True
            with open(self._path, "a", encoding="utf-8") as file:
                if need_leading_newline:
                    file.write("\n")
                file.write(line + "\n")

    # ── Queries ──────────────────────────────────────────────────────────────

    def iter_events(self, *, since: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield events in disk order. Skip corrupt/non-mapping lines.

        ``since`` is an ISO timestamp string — events whose ``ts`` is
        lexicographically less are skipped (ISO-8601 sort = chronological).
        """
        if not os.path.isfile(self._path):
            return
        with self._lock, open(self._path, encoding="utf-8") as file:
            for raw in file:
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if since and str(event.get("ts", "")) < since:
                    continue
                yield event

    def events_for_isrc(self, isrc: str) -> list[dict[str, Any]]:
        """Return all events whose ``isrc`` matches (case-insensitive)."""
        if not isrc:
            return []
        target = isrc.upper()
        return [
            event
            for event in self.iter_events()
            if str(event.get("isrc") or "").upper() == target
        ]

    def count(self) -> int:
        """Return the number of valid events on disk."""
        return sum(1 for _ in self.iter_events())

    def artist_affinity(
        self,
        *,
        window_days: int = DEFAULT_AFFINITY_WINDOW_DAYS,
        min_samples: int = DEFAULT_MIN_SAMPLES,
    ) -> dict[str, float]:
        """Per-artist affinity, computed as mean weight over the window.

        Returns ``{artist_lowercase: score}`` where score is the mean of
        the weights of contributing events, clipped to ``[-1.0, 1.0]``.
        Artists with fewer than ``min_samples`` contributing events are
        omitted (too noisy).
        """
        return self._affinity("artist", window_days=window_days, min_samples=min_samples)

    def genre_affinity(
        self,
        *,
        window_days: int = DEFAULT_AFFINITY_WINDOW_DAYS,
        min_samples: int = DEFAULT_MIN_SAMPLES,
    ) -> dict[str, float]:
        """Per-genre affinity. Same formula as ``artist_affinity``."""
        return self._affinity("genre", window_days=window_days, min_samples=min_samples)

    # ── Private ──────────────────────────────────────────────────────────────

    def _affinity(
        self, key: str, *, window_days: int, min_samples: int
    ) -> dict[str, float]:
        cutoff = (datetime.now(UTC) - timedelta(days=window_days)).isoformat(
            timespec="seconds"
        )
        sums: dict[str, float] = {}
        counts: dict[str, int] = {}
        for event in self.iter_events(since=cutoff):
            weight = _weight_for(event)
            if weight == 0.0:
                continue
            bucket_raw = event.get(key)
            if not isinstance(bucket_raw, str) or not bucket_raw.strip():
                continue
            bucket = bucket_raw.strip().lower()
            sums[bucket] = sums.get(bucket, 0.0) + weight
            counts[bucket] = counts.get(bucket, 0) + 1
        result: dict[str, float] = {}
        for bucket, total in counts.items():
            if total < min_samples:
                continue
            score = sums[bucket] / total
            result[bucket] = max(-1.0, min(1.0, score))
        return result


# ── Private Functions ────────────────────────────────────────────────────────


def _weight_for(event: dict[str, Any]) -> float:
    """Return the affinity weight of an event. 0.0 means ignore.

    - ``loved_delta`` counts only when ``to_loved is True`` (un-love
      is treated as a change of mind, not a rejection).
    - ``playcount_delta`` counts only when ``delta > 0`` (a non-positive
      delta carries no signal).
    """
    event_type = str(event.get("type") or "")
    if event_type == "loved_delta":
        return _EVENT_WEIGHTS[event_type] if event.get("to_loved") is True else 0.0
    if event_type == "playcount_delta":
        try:
            delta = int(event.get("delta", 0))
        except (TypeError, ValueError):
            return 0.0
        return _EVENT_WEIGHTS[event_type] if delta > 0 else 0.0
    return _EVENT_WEIGHTS.get(event_type, 0.0)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
