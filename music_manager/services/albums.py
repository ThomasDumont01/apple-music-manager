"""Albums store — in-memory Deezer album cache backed by albums.json.

Caches album data fetched from Deezer to avoid redundant API calls.
Saves incrementally (after each put) since album data is expensive to fetch.
"""

import threading

from music_manager.core.io import load_json, save_json

# ── Entry point ──────────────────────────────────────────────────────────────


class Albums:
    """In-memory cache for Deezer album data."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._data: dict[str, dict] = {
            k: v for k, v in load_json(path).items() if isinstance(v, dict)
        }
        self._dirty = False

    def get(self, album_id: int) -> dict | None:
        """Return a copy of cached album data, or None if not cached.

        Returns a shallow copy to prevent callers from mutating the cache.
        """
        with self._lock:
            data = self._data.get(str(album_id))
            return dict(data) if data is not None else None

    def put(self, album_id: int, data: dict) -> None:
        """Cache album data in memory. Call save() to persist."""
        with self._lock:
            self._data[str(album_id)] = data
            self._dirty = True

    def remove(self, album_id: int | str) -> None:
        """Remove an album from cache."""
        with self._lock:
            if self._data.pop(str(album_id), None) is not None:
                self._dirty = True

    def save(self) -> None:
        """Save cache to disk if modified."""
        with self._lock:
            if self._dirty:
                save_json(self._path, self._data)
                self._dirty = False

    def all(self) -> dict[str, dict]:
        """Return all cached albums."""
        return self._data
