"""Tracks store — in-memory manager for tracks.json.

Single source of truth for all track data during a session. Loads once
at startup, provides O(1) lookups by apple_id and ISRC, saves to disk
at checkpoints (not on every update).
"""

import threading

from music_manager.core.io import load_json, save_json

# ── Entry point ──────────────────────────────────────────────────────────────


class Tracks:
    """In-memory store for tracks.json with dual indexing.

    Thread-safe: all mutations are protected by a reentrant lock.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._data: dict[str, dict] = {
            k: v for k, v in load_json(path).items() if isinstance(v, dict)
        }
        self._by_isrc: dict[str, str] = {}
        self._by_title_artist: dict[str, str] = {}  # "norm_title:norm_artist" → apple_id
        self._dirty = False
        self._build_indexes()

    def get_by_apple_id(self, apple_id: str) -> dict | None:
        """O(1) lookup by apple_id (primary key)."""
        with self._lock:
            return self._data.get(apple_id)

    def get_by_isrc(self, isrc: str) -> dict | None:
        """O(1) lookup by ISRC (secondary index, case-insensitive)."""
        with self._lock:
            apple_id = self._by_isrc.get(isrc.upper())
            if apple_id:
                return self._data.get(apple_id)
            return None

    def get_by_title_artist(self, norm_title: str, norm_artist: str) -> dict | None:
        """O(1) lookup by normalized title+artist."""
        with self._lock:
            apple_id = self._by_title_artist.get(f"{norm_title}:{norm_artist}")
            if apple_id:
                return self._data.get(apple_id)
            return None

    def add(self, apple_id: str, entry: dict) -> None:
        """Add a new entry. Overwrites if apple_id already exists."""
        with self._lock:
            # Clean ALL old indexes if overwriting
            old = self._data.get(apple_id)
            if old:
                old_isrc = old.get("isrc", "")
                if old_isrc and self._by_isrc.get(old_isrc.upper()) == apple_id:
                    del self._by_isrc[old_isrc.upper()]
                self._remove_title_artist_index(apple_id, old)
            self._data[apple_id] = entry
            isrc = entry.get("isrc", "")
            if isrc:
                self._by_isrc[isrc.upper()] = apple_id
            self._index_title_artist(apple_id, entry)
            self._dirty = True

    def update(self, apple_id: str, updates: dict) -> None:
        """Update fields on an existing entry. No-op if apple_id not found."""
        with self._lock:
            entry = self._data.get(apple_id)
            if entry is None:
                return
            old_isrc = entry.get("isrc", "")
            if any(k in updates for k in ("title", "artist", "csv_title", "csv_artist")):
                self._remove_title_artist_index(apple_id, entry)
            entry.update(updates)
            new_isrc = entry.get("isrc", "")
            if new_isrc and new_isrc.upper() != (old_isrc or "").upper():
                if old_isrc and self._by_isrc.get(old_isrc.upper()) == apple_id:
                    del self._by_isrc[old_isrc.upper()]
                self._by_isrc[new_isrc.upper()] = apple_id
            if any(k in updates for k in ("title", "artist", "csv_title", "csv_artist")):
                self._index_title_artist(apple_id, entry)
            self._dirty = True

    def remove(self, apple_id: str) -> None:
        """Remove an entry by apple_id."""
        with self._lock:
            entry = self._data.pop(apple_id, None)
            if entry:
                isrc = entry.get("isrc", "")
                if isrc and self._by_isrc.get(isrc.upper()) == apple_id:
                    del self._by_isrc[isrc.upper()]
                self._remove_title_artist_index(apple_id, entry)
                self._dirty = True

    def all(self) -> dict[str, dict]:
        """Return all entries (reference, not copy)."""
        return self._data

    def without_isrc(self) -> list[tuple[str, dict]]:
        """Return entries without ISRC as [(apple_id, entry), ...]."""
        return [
            (apple_id, entry) for apple_id, entry in self._data.items() if not entry.get("isrc")
        ]

    def mark_dirty(self) -> None:
        """Mark store as modified (external file_path sync)."""
        self._dirty = True

    def save(self) -> None:
        """Write to disk if modified since last save."""
        if self._dirty:
            save_json(self._path, self._data)
            self._dirty = False

    # ── Private Functions ────────────────────────────────────────────────────

    def _index_title_artist(self, apple_id: str, entry: dict) -> None:
        """Index a single entry by normalized title+artist."""
        from music_manager.core.normalize import normalize  # noqa: PLC0415

        title = normalize(entry.get("title") or "")
        artist = normalize(entry.get("artist") or "")
        if title and artist:
            self._by_title_artist[f"{title}:{artist}"] = apple_id
        csv_title_stored = entry.get("csv_title") or ""
        if csv_title_stored:
            csv_key = f"{normalize(csv_title_stored)}:{normalize(entry.get('csv_artist') or '')}"
            self._by_title_artist.setdefault(csv_key, apple_id)

    def _remove_title_artist_index(self, apple_id: str, entry: dict) -> None:
        """Remove title+artist index entries for an apple_id."""
        from music_manager.core.normalize import normalize  # noqa: PLC0415

        title = normalize(entry.get("title", ""))
        artist = normalize(entry.get("artist", ""))
        if title and artist:
            key = f"{title}:{artist}"
            if self._by_title_artist.get(key) == apple_id:
                del self._by_title_artist[key]
        csv_title_stored = entry.get("csv_title") or ""
        if csv_title_stored:
            csv_key = f"{normalize(csv_title_stored)}:{normalize(entry.get('csv_artist') or '')}"
            if self._by_title_artist.get(csv_key) == apple_id:
                del self._by_title_artist[csv_key]

    def _build_indexes(self) -> None:
        """Build secondary indexes from loaded data."""
        for apple_id, entry in self._data.items():
            isrc = entry.get("isrc", "")
            if isrc:
                self._by_isrc[isrc.upper()] = apple_id
            self._index_title_artist(apple_id, entry)
